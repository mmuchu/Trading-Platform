import logging
from typing import Optional
from core.strategies.momentum import MomentumStrategy
from core.strategies.breakout import BreakoutStrategy
from core.execution.paper_broker import PaperBroker
from core.risk.risk_engine import RiskEngine
from core.analytics.pnl import PnlTracker
from core.analytics.metrics import Metrics
from core.data.historical_loader import HistoricalLoader
from config.settings import settings
logger = logging.getLogger(__name__)
STRATEGY_MAP = {"momentum": MomentumStrategy, "breakout": BreakoutStrategy}

class BacktestResult:
    def __init__(self, broker, tracker, trades, report):
        self.broker_snapshot = broker.snapshot(broker.trades[-1].price if broker.trades else broker.initial_cash)
        self.pnl_summary = tracker.summary()
        self.metrics_report = report
        self.trades = trades
        self.equity_curve = tracker.equity_series()
    def to_dict(self):
        return {"broker":self.broker_snapshot,"pnl":self.pnl_summary,"metrics":self.metrics_report,"trade_count":len(self.trades),"equity_curve_length":len(self.equity_curve)}

def run_backtest(strategy_name="momentum", prices=None, symbol=None, interval="1m", kline_limit=500, initial_cash=None):
    cls = STRATEGY_MAP.get(strategy_name)
    if not cls: raise ValueError(f"Unknown strategy: {strategy_name}")
    if prices is None:
        loader = HistoricalLoader(symbol=symbol)
        prices = loader.fetch_close_prices(interval=interval, limit=kline_limit)
    if not prices: raise ValueError("No price data")
    logger.info("Backtest: strategy=%s bars=%d", strategy_name, len(prices))
    strategy = cls(); broker = PaperBroker(initial_cash=initial_cash); risk = RiskEngine()
    tracker = PnlTracker(broker.initial_cash); history = []
    for i, price in enumerate(prices):
        history.append(price)
        signal = strategy.generate(price, history); equity = broker.equity(price)
        safe = risk.validate(signal, broker.position, equity)
        if safe not in ("BLOCK","HOLD"): broker.execute(safe, price, timestamp=float(i))
        tracker.record(timestamp=float(i),equity=broker.equity(price),cash=broker.cash,position=broker.position,current_price=price,realised_pnl=broker.realised_pnl)
    tl = [{"pnl":t.equity_after-broker.initial_cash,"side":t.side,"price":t.price} for t in broker.trades]
    report = Metrics.full_report(tracker.equity_series(), tl, 252)
    result = BacktestResult(broker, tracker, broker.get_trades(), report)
    logger.info("Backtest done: return=%.2f%% trades=%d", report["total_return_pct"], report["total_trades"])
    return result