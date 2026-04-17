import json, logging, time as _time, threading, asyncio
from typing import Callable, Optional
from core.data.binance_ws import BinanceFeed
from core.strategies.momentum import MomentumStrategy
from core.strategies.breakout import BreakoutStrategy
from core.execution.paper_broker import PaperBroker
from core.execution.binance_broker import BinanceBroker
from core.risk.risk_engine import RiskEngine
from core.analytics.pnl import PnlTracker
from config.settings import settings
logger = logging.getLogger(__name__)
STRATEGY_MAP = {"momentum": MomentumStrategy, "breakout": BreakoutStrategy}

class LiveEngine:
    def __init__(self, strategy_name="momentum", mode="PAPER", on_tick=None):
        self.mode = mode.upper(); self.strategy_name = strategy_name
        StrategyCls = STRATEGY_MAP.get(strategy_name, MomentumStrategy)
        self.strategy = StrategyCls(); self.broker = PaperBroker(); self.risk = RiskEngine()
        self.tracker = PnlTracker(self.broker.initial_cash); self.feed = BinanceFeed()
        self.live_broker = BinanceBroker(testnet=True) if mode.upper()=="LIVE" else None
        self._on_tick = on_tick; self._running = False; self._tick_count = 0

    def start(self):
        if self._running: return
        self._running = True
        logger.info("Live engine: strategy=%s mode=%s symbol=%s", self.strategy_name, self.mode, self.feed.symbol)
        self.feed.start()
        threading.Thread(target=self._tick_loop, daemon=True).start()

    def stop(self):
        self._running = False; self.feed.stop()
        logger.info("Live engine stopped after %d ticks", self._tick_count)

    def get_status(self):
        price = self.feed.get_price()
        snap = self.broker.snapshot(price) if price else None
        return {"running":self._running,"mode":self.mode,"strategy":self.strategy_name,"symbol":self.feed.symbol,"tick_count":self._tick_count,"current_price":price,"portfolio":snap,"risk":{"drawdown_pct":round(self.risk.state.current_drawdown_pct*100,2),"blocked_count":self.risk.state.blocked_count,"last_block_reason":self.risk.state.last_block_reason,"halted":self.risk._halted},"pnl":self.tracker.summary()}

    def _tick_loop(self):
        while self._running:
            price = self.feed.get_price()
            if price is None: _time.sleep(0.5); continue
            history = self.feed.get_history(); signal = self.strategy.generate(price, history)
            equity = self.broker.equity(price); safe = self.risk.validate(signal, self.broker.position, equity)
            executed = False
            if safe not in ("BLOCK","HOLD"):
                self.broker.execute(safe, price); executed = True
                if self.mode=="LIVE" and self.live_broker:
                    self.live_broker.create_order(symbol=self.feed.symbol, side=safe, quantity=1)
            self.tracker.record(timestamp=_time.time(), equity=equity, cash=self.broker.cash, position=self.broker.position, current_price=price, realised_pnl=self.broker.realised_pnl)
            self._tick_count += 1
            if self._on_tick:
                try: self._on_tick({"tick":self._tick_count,"price":price,"signal":signal,"safe_signal":safe,"executed":executed,"portfolio":self.broker.snapshot(price)})
                except: pass
            _time.sleep(1.0)

def run_live(strategy_name="momentum", mode="PAPER", on_tick=None):
    engine = LiveEngine(strategy_name=strategy_name, mode=mode, on_tick=on_tick)
    engine.start(); return engine