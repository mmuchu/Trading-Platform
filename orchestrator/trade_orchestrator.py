import logging
from typing import Dict, List, Optional, Callable
from datetime import datetime
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class OrchestratorConfig:
    auto_execute: bool = False
    max_concurrent_positions: int = 10
    default_risk_per_trade_pct: float = 1.0
    enable_stop_loss: bool = True
    enable_take_profit: bool = True
    slippage_pct: float = 0.1


class TradeOrchestrator:

    def __init__(self, config: OrchestratorConfig = None,
                 risk_manager=None, stop_loss_manager=None, take_profit_manager=None,
                 position_sizer=None, exposure_monitor=None, max_drawdown_guard=None,
                 event_bus=None, portfolio_manager=None, order_manager=None,
                 signal_processor=None, market_analyzer=None, strategy_engine=None,
                 performance_tracker=None, risk_alert_manager=None):
        self.config = config or OrchestratorConfig()
        self._risk_manager = risk_manager
        self._sl_manager = stop_loss_manager
        self._tp_manager = take_profit_manager
        self._position_sizer = position_sizer
        self._exposure_monitor = exposure_monitor
        self._dd_guard = max_drawdown_guard
        self._event_bus = event_bus
        self._portfolio = portfolio_manager
        self._order_mgr = order_manager
        self._signal_proc = signal_processor
        self._market_analyzer = market_analyzer
        self._strategy_engine = strategy_engine
        self._perf_tracker = performance_tracker
        self._alert_mgr = risk_alert_manager
        self._is_running = False
        self._tick_counter = 0
        self._last_status_time = None
        logger.info("TradeOrchestrator initialized")

    def start(self):
        self._is_running = True
        if self._dd_guard and self._portfolio:
            snap = self._portfolio.get_snapshot()
            self._dd_guard.initialize(snap.total_value)
        logger.info("TradeOrchestrator STARTED")
        if self._event_bus:
            self._event_bus.publish(self._event_bus.EventType.SYSTEM_INFO, {"message": "Orchestrator started"})

    def stop(self):
        self._is_running = False
        logger.info("TradeOrchestrator STOPPED")
        if self._event_bus:
            self._event_bus.publish(self._event_bus.EventType.SYSTEM_INFO, {"message": "Orchestrator stopped"})

    def on_tick(self, price_map: Dict[str, float]):
        if not self._is_running:
            return
        self._tick_counter += 1
        if self._portfolio:
            self._portfolio.update_prices(price_map)
        if self._exposure_monitor:
            self._exposure_monitor.update_prices(price_map)
        if self._sl_manager:
            for symbol, price in price_map.items():
                triggered, order = self._sl_manager.check_stop_triggered(symbol, price)
                if triggered and order:
                    self._handle_stop_loss(order, price)
        if self._tp_manager:
            for symbol, price in price_map.items():
                triggered, result = self._tp_manager.check_tp_triggered(symbol, price)
                if triggered and result:
                    self._handle_take_profit(result, price)
        if self._dd_guard and self._portfolio:
            snap = self._portfolio.get_snapshot()
            state = self._dd_guard.update_equity(snap.total_value)
            if state.is_halted:
                logger.critical(f"Trading HALTED: {state.halt_reason}")
        if self._tick_counter % 60 == 0:
            self._evaluate_alerts()

    def on_bar(self, candles: Dict[str, List[Dict]], market_data: Dict = None):
        if not self._is_running:
            return
        if self._market_analyzer:
            for symbol, candle_list in candles.items():
                self._market_analyzer.update_ohlcv(symbol, candle_list)
        if self._strategy_engine:
            results = self._strategy_engine.run_all(candles, market_data)
            for result in results:
                for signal in result.signals:
                    self._process_signal(signal)

    def _process_signal(self, signal: Dict):
        symbol = signal.get("symbol", "")
        if not symbol:
            return
        if self._dd_guard and self._dd_guard.is_halted():
            logger.warning(f"Signal rejected - trading halted: {symbol}")
            return
        if self._portfolio and self._portfolio.has_position(symbol):
            logger.debug(f"Signal skipped - position exists: {symbol}")
            return
        if self._risk_manager and self._portfolio:
            snap = self._portfolio.get_snapshot()
            assessment = self._risk_manager.assess_trade(
                symbol=symbol, side=signal.get("side", "buy"),
                entry_price=signal.get("entry_price", 0),
                portfolio_value=snap.total_value,
                current_exposure=snap.positions_value,
                open_positions=snap.num_positions,
                signal_strength=signal.get("strength", 0.5),
            )
            if not assessment.approved:
                logger.warning(f"Signal rejected by risk: {symbol} - {assessment.reasons}")
                if self._event_bus:
                    self._event_bus.publish(self._event_bus.EventType.SIGNAL_REJECTED, {"symbol": symbol, "reasons": assessment.reasons})
                return
            signal["stop_loss"] = assessment.suggested_stop_loss
            signal["take_profit"] = assessment.suggested_take_profit
        if self.config.auto_execute:
            self._execute_signal(signal)
        else:
            logger.info(f"Signal queued (manual mode): {signal.get('side', '?')} {symbol}")

    def execute_signal(self, signal: Dict) -> bool:
        return self._execute_signal(signal)

    def _execute_signal(self, signal: Dict) -> bool:
        symbol = signal.get("symbol", "")
        side = signal.get("side", "buy")
        entry = signal.get("entry_price", 0)
        sl = signal.get("stop_loss", 0)
        tp = signal.get("take_profit", 0)
        if not entry or not symbol:
            logger.error("Invalid signal: missing symbol or entry_price")
            return False
        if not self._portfolio:
            logger.error("PortfolioManager not configured")
            return False
        snap = self._portfolio.get_snapshot()
        if self._position_sizer:
            from risk_guard.position_sizer import SizingMethod
            ps = self._position_sizer.calculate_size(
                symbol=symbol, entry_price=entry, stop_loss_price=sl,
                portfolio_value=snap.total_value, signal_strength=signal.get("strength", 0.5),
            )
            quantity = ps.quantity
        else:
            quantity = signal.get("quantity", 0)
        if quantity <= 0:
            logger.warning(f"Position size is 0 for {symbol}")
            return False
        holding = self._portfolio.open_position(symbol, side, quantity, entry)
        if not holding:
            logger.error(f"Failed to open position: {symbol}")
            return False
        if self._event_bus:
            self._event_bus.publish(self._event_bus.EventType.POSITION_OPENED, {"symbol": symbol, "side": side, "quantity": quantity, "price": entry})
        if self.config.enable_stop_loss and sl > 0 and self._sl_manager:
            self._sl_manager.set_fixed_stop(symbol, side, entry, abs(entry - sl) / entry * 100, quantity)
        if self.config.enable_take_profit and tp > 0 and self._tp_manager:
            self._tp_manager.set_fixed_tp(symbol, side, entry, abs(tp - entry) / entry * 100, quantity)
        if self._risk_manager:
            self._risk_manager.record_trade({"symbol": symbol, "side": side, "entry": entry, "quantity": quantity})
        logger.info(f"EXECUTED: {side} {quantity} {symbol} @ {entry}")
        return True

    def _handle_stop_loss(self, sl_order, price: float):
        symbol = sl_order.symbol
        logger.warning(f"Stop loss hit for {symbol} @ {price}")
        if self._portfolio:
            pnl, comm = self._portfolio.close_position(symbol, price=price)
            if self._perf_tracker:
                holding = self._portfolio.get_holding(symbol)
                self._perf_tracker.record_trade(symbol, sl_order.side, holding.open_time if holding else "", datetime.now().isoformat(), sl_order.entry_price, price, sl_order.quantity, comm)
            if self._tp_manager:
                self._tp_manager.remove_tp(symbol)
            if self._event_bus:
                self._event_bus.publish(self._event_bus.EventType.STOP_LOSS_TRIGGERED, {"symbol": symbol, "price": price})

    def _handle_take_profit(self, tp_result: Dict, price: float):
        symbol = tp_result["symbol"]
        logger.info(f"Take profit hit for {symbol} @ {price}")
        if self._portfolio:
            self._portfolio.close_position(symbol, quantity=tp_result.get("quantity", 0), price=price)
            if self._sl_manager:
                self._sl_manager.remove_stop(symbol)
            if self._event_bus:
                self._event_bus.publish(self._event_bus.EventType.TAKE_PROFIT_TRIGGERED, {"symbol": symbol, "price": price})

    def _evaluate_alerts(self):
        if not self._alert_mgr or not self._portfolio:
            return
        snap = self._portfolio.get_snapshot()
        ctx = {
            "total_exposure_pct": snap.positions_value / snap.total_value * 100 if snap.total_value > 0 else 0,
            "drawdown_pct": 0,
            "largest_position_pct": 0,
        }
        if self._exposure_monitor:
            es = self._exposure_monitor.get_snapshot(snap.total_value)
            ctx["total_exposure_pct"] = es.total_exposure_pct
            ctx["largest_position_pct"] = es.largest_position_pct
        if self._dd_guard:
            dd_state = self._dd_guard.get_status()
            ctx["drawdown_pct"] = dd_state.get("current_drawdown_pct", 0)
        self._alert_mgr.evaluate(ctx)

    def get_status(self) -> Dict:
        return {
            "is_running": self._is_running,
            "tick_count": self._tick_counter,
            "auto_execute": self.config.auto_execute,
            "modules": {
                "risk_manager": self._risk_manager is not None,
                "portfolio": self._portfolio is not None,
                "order_manager": self._order_mgr is not None,
                "event_bus": self._event_bus is not None,
                "stop_loss": self._sl_manager is not None,
                "take_profit": self._tp_manager is not None,
                "exposure": self._exposure_monitor is not None,
                "drawdown_guard": self._dd_guard is not None,
            },
        }
