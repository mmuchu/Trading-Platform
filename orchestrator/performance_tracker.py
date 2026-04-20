import logging
import math
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    trade_id: str
    symbol: str
    side: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    quantity: float
    pnl: float
    pnl_pct: float
    commission: float
    net_pnl: float
    holding_duration_seconds: float
    strategy_name: str
    tags: List[str] = field(default_factory=list)


@dataclass
class PerformanceSummary:
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    total_pnl: float
    total_commission: float
    net_pnl: float
    avg_win: float
    avg_loss: float
    avg_win_pct: float
    avg_loss_pct: float
    profit_factor: float
    max_consecutive_wins: int
    max_consecutive_losses: int
    avg_holding_time_seconds: float
    best_trade_pnl: float
    worst_trade_pnl: float
    Sharpe: float = 0.0
    sortino: float = 0.0
    max_drawdown_pct: float = 0.0


class PerformanceTracker:

    def __init__(self, initial_capital: float = 100000.0):
        self._initial_capital = initial_capital
        self._trades: List[TradeRecord] = []
        self._equity_curve: List[Dict] = []
        self._daily_returns: List[float] = []
        self._counter = 0
        self._by_symbol: Dict[str, List[TradeRecord]] = defaultdict(list)
        self._by_strategy: Dict[str, List[TradeRecord]] = defaultdict(list)
        self._by_date: Dict[str, List[TradeRecord]] = defaultdict(list)
        logger.info(f"PerformanceTracker initialized (capital={initial_capital})")

    def record_trade(self, symbol: str, side: str, entry_time: str, exit_time: str,
                     entry_price: float, exit_price: float, quantity: float,
                     commission: float, strategy_name: str = "", tags: List[str] = None) -> TradeRecord:
        if side == "buy":
            pnl = (exit_price - entry_price) * quantity
        else:
            pnl = (entry_price - exit_price) * quantity
        pnl_pct = pnl / (entry_price * quantity) * 100 if entry_price * quantity > 0 else 0
        net_pnl = pnl - commission
        try:
            entry_dt = datetime.fromisoformat(entry_time)
            exit_dt = datetime.fromisoformat(exit_time)
            duration = (exit_dt - entry_dt).total_seconds()
        except (ValueError, TypeError):
            duration = 0
        self._counter += 1
        trade = TradeRecord(
            trade_id=f"TRD-{self._counter:06d}", symbol=symbol, side=side,
            entry_time=entry_time, exit_time=exit_time,
            entry_price=entry_price, exit_price=exit_price, quantity=quantity,
            pnl=round(pnl, 2), pnl_pct=round(pnl_pct, 4),
            commission=round(commission, 2), net_pnl=round(net_pnl, 2),
            holding_duration_seconds=duration, strategy_name=strategy_name,
            tags=tags or [],
        )
        self._trades.append(trade)
        self._by_symbol[symbol].append(trade)
        if strategy_name:
            self._by_strategy[strategy_name].append(trade)
        try:
            date_key = exit_time[:10]
            self._by_date[date_key].append(trade)
        except (IndexError, TypeError):
            pass
        logger.debug(f"Trade recorded: {trade.trade_id} {symbol} pnl={net_pnl:.2f}")
        return trade

    def update_equity(self, equity_value: float):
        self._equity_curve.append({
            "timestamp": datetime.now().isoformat(),
            "equity": equity_value,
        })
        if len(self._equity_curve) >= 2:
            prev = self._equity_curve[-2]["equity"]
            ret = (equity_value - prev) / prev if prev > 0 else 0
            self._daily_returns.append(ret)

    def get_summary(self, trades: List[TradeRecord] = None) -> PerformanceSummary:
        t = trades or self._trades
        if not t:
            return PerformanceSummary(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        wins = [tr for tr in t if tr.net_pnl > 0]
        losses = [tr for tr in t if tr.net_pnl < 0]
        total_pnl = sum(tr.pnl for tr in t)
        total_commission = sum(tr.commission for tr in t)
        net = sum(tr.net_pnl for tr in t)
        avg_win = sum(tr.net_pnl for tr in wins) / len(wins) if wins else 0
        avg_loss = sum(tr.net_pnl for tr in losses) / len(losses) if losses else 0
        avg_win_pct = sum(tr.pnl_pct for tr in wins) / len(wins) if wins else 0
        avg_loss_pct = sum(tr.pnl_pct for tr in losses) / len(losses) if losses else 0
        gross_profit = sum(tr.pnl for tr in wins)
        gross_loss = abs(sum(tr.pnl for tr in losses))
        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        max_cw, max_cl = self._consecutive_streaks(t)
        avg_dur = sum(tr.holding_duration_seconds for tr in t) / len(t)
        best = max(tr.net_pnl for tr in t)
        worst = min(tr.net_pnl for tr in t)
        returns = self._daily_returns if not trades else []
        sharpe = self._calc_sharpe(returns)
        sortino = self._calc_sortino(returns)
        dd = self._calc_max_dd(self._equity_curve)
        return PerformanceSummary(
            total_trades=len(t), winning_trades=len(wins), losing_trades=len(losses),
            win_rate=round(len(wins) / len(t), 4) if t else 0,
            total_pnl=round(total_pnl, 2), total_commission=round(total_commission, 2),
            net_pnl=round(net, 2), avg_win=round(avg_win, 2), avg_loss=round(avg_loss, 2),
            avg_win_pct=round(avg_win_pct, 4), avg_loss_pct=round(avg_loss_pct, 4),
            profit_factor=round(pf, 4) if pf != float("inf") else 999.99,
            max_consecutive_wins=max_cw, max_consecutive_losses=max_cl,
            avg_holding_time_seconds=round(avg_dur, 2),
            best_trade_pnl=round(best, 2), worst_trade_pnl=round(worst, 2),
            Sharpe=round(sharpe, 4), sortino=round(sortino, 4), max_drawdown_pct=round(dd, 4),
        )

    def get_summary_by_symbol(self, symbol: str) -> PerformanceSummary:
        return self.get_summary(self._by_symbol.get(symbol, []))

    def get_summary_by_strategy(self, strategy: str) -> PerformanceSummary:
        return self.get_summary(self._by_strategy.get(strategy, []))

    def get_summary_by_date_range(self, start: str, end: str) -> PerformanceSummary:
        filtered = [t for t in self._trades if start <= t.exit_time[:10] <= end]
        return self.get_summary(filtered)

    def get_trades(self, symbol: str = "", strategy: str = "", limit: int = 100) -> List[TradeRecord]:
        trades = self._trades
        if symbol:
            trades = [t for t in trades if t.symbol == symbol]
        if strategy:
            trades = [t for t in trades if t.strategy_name == strategy]
        return trades[-limit:]

    def get_equity_curve(self) -> List[Dict]:
        return list(self._equity_curve)

    def _consecutive_streaks(self, trades: List[TradeRecord]) -> Tuple[int, int]:
        max_wins, max_losses, cw, cl = 0, 0, 0, 0
        for t in trades:
            if t.net_pnl > 0:
                cw += 1
                cl = 0
            elif t.net_pnl < 0:
                cl += 1
                cw = 0
            else:
                cw, cl = 0, 0
            max_wins = max(max_wins, cw)
            max_losses = max(max_losses, cl)
        return max_wins, max_losses

    def _calc_sharpe(self, returns: List[float]) -> float:
        if len(returns) < 2:
            return 0.0
        mean = sum(returns) / len(returns)
        std = math.sqrt(sum((r - mean) ** 2 for r in returns) / (len(returns) - 1))
        rf = 0.02 / 252
        return (mean - rf) / std if std > 0 else 0.0

    def _calc_sortino(self, returns: List[float]) -> float:
        if len(returns) < 2:
            return 0.0
        mean = sum(returns) / len(returns)
        downside = [r for r in returns if r < 0]
        if not downside:
            return 0.0
        dd = math.sqrt(sum(r ** 2 for r in downside) / len(downside))
        rf = 0.02 / 252
        return (mean - rf) / dd if dd > 0 else 0.0

    def _calc_max_dd(self, curve: List[Dict]) -> float:
        if len(curve) < 2:
            return 0.0
        peak = curve[0]["equity"]
        max_dd = 0.0
        for point in curve:
            if point["equity"] > peak:
                peak = point["equity"]
            dd = (peak - point["equity"]) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)
        return max_dd * 100

    def get_status(self) -> Dict:
        summary = self.get_summary()
        return {
            "total_trades": summary.total_trades,
            "win_rate": summary.win_rate,
            "net_pnl": summary.net_pnl,
            "profit_factor": summary.profit_factor,
            "symbols_tracked": list(self._by_symbol.keys()),
            "strategies_tracked": list(self._by_strategy.keys()),
            "equity_points": len(self._equity_curve),
        }
