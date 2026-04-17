import argparse, logging, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config.settings import settings
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(name)-30s  %(levelname)-5s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("main")

def main():
    p = argparse.ArgumentParser(description="Trading Platform")
    p.add_argument("--mode",choices=["dashboard","live","backtest"],default="dashboard")
    p.add_argument("--strategy",choices=["momentum","breakout"],default="momentum")
    p.add_argument("--symbol",default=None)
    p.add_argument("--bars",type=int,default=500)
    p.add_argument("--cash",type=float,default=None)
    p.add_argument("--port",type=int,default=None)
    a = p.parse_args()
    if a.symbol: settings.binance.symbol = a.symbol.lower()
    if a.port: settings.dashboard.port = a.port
    logger.info("Trading Platform v1.0 | Mode: %s | Strategy: %s | Symbol: %s", a.mode.upper(), a.strategy, settings.binance.symbol)

    if a.mode == "dashboard":
        import uvicorn; cfg = settings.dashboard
        logger.info("Dashboard: http://%s:%d", cfg.host, cfg.port)
        uvicorn.run("dashboard.app:app", host=cfg.host, port=cfg.port, reload=cfg.reload)
    elif a.mode == "live":
        from core.engine.live import run_live; import time
        logger.info("Starting live engine (Paper mode)...")
        engine = run_live(strategy_name=a.strategy, mode="PAPER")
        try:
            while True:
                time.sleep(5); s = engine.get_status()
                logger.info("Tick %d | Price: %s | Equity: %s | Trades: %d", s["tick_count"], s["current_price"], s["portfolio"]["equity"] if s["portfolio"] else "N/A", s["portfolio"]["total_trades"] if s["portfolio"] else 0)
        except KeyboardInterrupt: engine.stop(); logger.info("Stopped")
    elif a.mode == "backtest":
        from core.engine.backtest import run_backtest
        logger.info("Backtest: strategy=%s bars=%d", a.strategy, a.bars)
        r = run_backtest(strategy_name=a.strategy, kline_limit=a.bars, initial_cash=a.cash)
        m = r.metrics_report
        logger.info("Return: %.2f%% | Max DD: %.2f%% | Sharpe: %s | Trades: %d | Win Rate: %s", m["total_return_pct"], m["max_drawdown_pct"], m["sharpe_ratio"], m["total_trades"], m["win_rate"])

if __name__ == "__main__": main()