import logging
import math
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


class RiskMetricType(Enum):
    VAR = "value_at_risk"
    SHARPE = "sharpe_ratio"
    SORTINO = "sortino_ratio"
    MAX_DRAWDOWN = "max_drawdown"
    WIN_RATE = "win_rate"
    PROFIT_FACTOR = "profit_factor"
    EXPECTED_VALUE = "expected_value"
    CALMAR = "calmar_ratio"
    VOLATILITY = "volatility"
    DAILY_RETURN = "daily_return"


@dataclass
class RiskMetrics:
    var_95: float = 0.0
    var_99: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    max_drawdown_pct: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expected_value: float = 0.0
    calmar_ratio: float = 0.0
    annualized_volatility: float = 0.0
    average_daily_return: float = 0.0
    total_trades: int = 0
    profitable_trades: int = 0
    losing_trades: int = 0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    avg_win_loss_ratio: float = 0.0


@dataclass
class CorrelationResult:
    symbol_a: str
    symbol_b: str
    correlation: float
    period_days: int


class RiskCalculator:

    def __init__(self, risk_free_rate: float = 0.02):
        self.risk_free_rate = risk_free_rate / 252
        self._price_cache: Dict[str, List[float]] = {}
        self._return_cache: Dict[str, List[float]] = {}
        logger.info(f"RiskCalculator initialized (risk_free_rate={risk_free_rate:.4f})")

    def calculate_all_metrics(self, returns: List[float], portfolio_value: float = 1.0) -> RiskMetrics:
        if not returns or len(returns) < 2:
            logger.warning("Insufficient return data for metrics calculation")
            return RiskMetrics()
        total = len(returns)
        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r < 0]
        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 0.0
        largest_win = max(wins) if wins else 0.0
        largest_loss = min(losses) if losses else 0.0
        avg_win = gross_profit / len(wins) if wins else 0.0
        avg_loss = gross_loss / len(losses) if losses else 0.0
        avg_wl = avg_win / avg_loss if avg_loss > 0 else float('inf')
        win_rate = len(wins) / total if total > 0 else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        avg_return = sum(returns) / total
        ev = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
        variance = sum((r - avg_return) ** 2 for r in returns) / (total - 1) if total > 1 else 0
        std = math.sqrt(variance)
        downside = [r for r in returns if r < 0]
        downside_var = sum(r ** 2 for r in downside) / len(downside) if downside else 0
        downside_dev = math.sqrt(downside_var)
        annualized_vol = std * math.sqrt(252)
        sharpe = (avg_return - self.risk_free_rate) / std if std > 0 else 0.0
        sortino = (avg_return - self.risk_free_rate) / downside_dev if downside_dev > 0 else 0.0
        var_95 = self._calculate_var(returns, 0.05)
        var_99 = self._calculate_var(returns, 0.01)
        dd_pct = self._calculate_max_drawdown(returns)
        annual_return = avg_return * 252
        calmar = annual_return / abs(dd_pct) if abs(dd_pct) > 0 else 0.0
        return RiskMetrics(
            var_95=round(var_95, 6),
            var_99=round(var_99, 6),
            sharpe_ratio=round(sharpe, 4),
            sortino_ratio=round(sortino, 4),
            max_drawdown_pct=round(dd_pct, 6),
            win_rate=round(win_rate, 4),
            profit_factor=round(profit_factor, 4) if profit_factor != float('inf') else 999.99,
            expected_value=round(ev, 6),
            calmar_ratio=round(calmar, 4),
            annualized_volatility=round(annualized_vol, 6),
            average_daily_return=round(avg_return, 6),
            total_trades=total,
            profitable_trades=len(wins),
            losing_trades=len(losses),
            largest_win=round(largest_win, 6),
            largest_loss=round(largest_loss, 6),
            avg_win=round(avg_win, 6),
            avg_loss=round(avg_loss, 6),
            avg_win_loss_ratio=round(avg_wl, 4) if avg_wl != float('inf') else 999.99,
        )

    def _calculate_var(self, returns: List[float], alpha: float) -> float:
        sorted_returns = sorted(returns)
        index = int(len(sorted_returns) * alpha)
        index = min(index, len(sorted_returns) - 1)
        return abs(sorted_returns[index])

    def _calculate_max_drawdown(self, returns: List[float]) -> float:
        peak = 1.0
        equity = 1.0
        max_dd = 0.0
        for r in returns:
            equity *= (1 + r)
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
        return max_dd

    def calculate_portfolio_var(self, positions: Dict[str, float], returns_map: Dict[str, List[float]], confidence: float = 0.95) -> float:
        total_var = 0.0
        alpha = 1 - confidence
        for symbol, weight in positions.items():
            if symbol in returns_map and returns_map[symbol]:
                var = self._calculate_var(returns_map[symbol], alpha)
                total_var += (weight * var) ** 2
        return math.sqrt(total_var)

    def calculate_correlation(self, returns_a: List[float], returns_b: List[float], symbol_a: str = "A", symbol_b: str = "B") -> CorrelationResult:
        min_len = min(len(returns_a), len(returns_b))
        if min_len < 2:
            return CorrelationResult(symbol_a, symbol_b, 0.0, min_len)
        a = returns_a[:min_len]
        b = returns_b[:min_len]
        mean_a = sum(a) / len(a)
        mean_b = sum(b) / len(b)
        cov = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(len(a)))
        var_a = sum((x - mean_a) ** 2 for x in a)
        var_b = sum((x - mean_b) ** 2 for x in b)
        denom = math.sqrt(var_a * var_b)
        corr = cov / denom if denom > 0 else 0.0
        return CorrelationResult(symbol_a, symbol_b, round(corr, 6), min_len)

    def estimate_position_risk(self, entry_price: float, position_size: float, stop_loss_price: float, take_profit_price: float) -> Dict:
        risk_per_unit = abs(entry_price - stop_loss_price)
        reward_per_unit = abs(take_profit_price - entry_price)
        total_risk = risk_per_unit * position_size
        total_reward = reward_per_unit * position_size
        rr_ratio = reward_per_unit / risk_per_unit if risk_per_unit > 0 else 0
        risk_pct = total_risk / (entry_price * position_size) * 100 if entry_price * position_size > 0 else 0
        reward_pct = total_reward / (entry_price * position_size) * 100 if entry_price * position_size > 0 else 0
        return {
            "risk_per_unit": round(risk_per_unit, 6),
            "reward_per_unit": round(reward_per_unit, 6),
            "total_risk": round(total_risk, 6),
            "total_reward": round(total_reward, 6),
            "risk_reward_ratio": round(rr_ratio, 4),
            "risk_pct": round(risk_pct, 4),
            "reward_pct": round(reward_pct, 4),
        }

    def calculate_kelly_fraction(self, win_rate: float, avg_win_pct: float, avg_loss_pct: float) -> float:
        if avg_loss_pct <= 0:
            return 0.0
        b = avg_win_pct / avg_loss_pct
        kelly = win_rate - ((1 - win_rate) / b)
        return max(0.0, round(kelly, 4))

    def compute_rolling_metrics(self, returns: List[float], window: int = 20) -> List[Dict]:
        results = []
        for i in range(window, len(returns) + 1):
            chunk = returns[i - window:i]
            metrics = self.calculate_all_metrics(chunk)
            results.append({
                "index": i,
                "sharpe_ratio": metrics.sharpe_ratio,
                "volatility": metrics.annualized_volatility,
                "win_rate": metrics.win_rate,
            })
        return results
