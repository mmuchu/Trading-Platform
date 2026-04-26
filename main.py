"""
Trading Platform — Unified Entry Point
========================================
Supports v1 modes (dashboard, live, backtest) AND v3 hybrid architecture.

Usage:
  python main.py                          # v1 dashboard (default)
  python main.py --mode live              # v1 live paper trading
  python main.py --mode backtest          # v1 backtest
  python main.py --mode v3                # v3 hybrid (sim + dashboard)
  python main.py --mode v3 --live         # v3 hybrid (live data + dashboard)
  python main.py --mode v3 --headless     # v3 hybrid (no dashboard)
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shutil
for _r,_ds,_fs in os.walk(os.path.dirname(os.path.abspath(__file__))):
    for _d in list(_ds):
        if _d=="__pycache__":
            shutil.rmtree(os.path.join(_r,_d),ignore_errors=True)
print("[main] __pycache__ cleared")

from config.settings import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-30s  %(levelname)-5s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("main")


def main():
    p = argparse.ArgumentParser(description="Trading Platform")
    p.add_argument(
        "--mode",
        choices=["dashboard", "live", "backtest", "v3"],
        default="dashboard",
        help="Run mode (default: dashboard)",
    )
    p.add_argument("--strategy", choices=["momentum", "breakout"], default="momentum")
    p.add_argument("--symbol", default=None)
    p.add_argument("--bars", type=int, default=500)
    p.add_argument("--cash", type=float, default=None)
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--live", action="store_true", help="(v3) Use live Binance data")
    p.add_argument("--headless", action="store_true", help="(v3) Run without dashboard")
    a = p.parse_args()

    if a.symbol:
        settings.binance.symbol = a.symbol.lower()
    if a.port:
        settings.dashboard.port = a.port

    # ── v3 Hybrid Architecture ────────────────────────────────────
    if a.mode == "v3":
        run_v3(live=a.live, headless=a.headless, port=a.port)
        return

    # ── v1 Original Modes ─────────────────────────────────────────
    logger.info(
        "Trading Platform v1 | Mode: %s | Strategy: %s | Symbol: %s",
        a.mode.upper(), a.strategy, settings.binance.symbol,
    )

    if a.mode == "dashboard":
        import uvicorn
        cfg = settings.dashboard
        logger.info("Dashboard: http://%s:%d", cfg.host, cfg.port)
        uvicorn.run("dashboard.app:app", host=cfg.host, port=cfg.port, reload=cfg.reload)

    elif a.mode == "live":
        import time
        from core.engine.live import run_live
        logger.info("Starting live engine (Paper mode)...")
        engine = run_live(strategy_name=a.strategy, mode="PAPER")
        try:
            while True:
                time.sleep(5)
                s = engine.get_status()
                logger.info(
                    "Tick %d | Price: %s | Equity: %s | Trades: %d",
                    s["tick_count"], s["current_price"],
                    s["portfolio"]["equity"] if s["portfolio"] else "N/A",
                    s["portfolio"]["total_trades"] if s["portfolio"] else 0,
                )
        except KeyboardInterrupt:
            engine.stop()
            logger.info("Stopped")

    elif a.mode == "backtest":
        from core.engine.backtest import run_backtest
        logger.info("Backtest: strategy=%s bars=%d", a.strategy, a.bars)
        r = run_backtest(strategy_name=a.strategy, kline_limit=a.bars, initial_cash=a.cash)
        m = r.metrics_report
        logger.info(
            "Return: %.2f%% | Max DD: %.2f%% | Sharpe: %s | Trades: %d | Win Rate: %s",
            m["total_return_pct"], m["max_drawdown_pct"],
            m["sharpe_ratio"], m["total_trades"], m["win_rate"],
        )


def run_v3(live: bool = False, headless: bool = False, port: int = None):
    """Launch the v3 hybrid architecture."""
    import asyncio
    import threading
    import time
    import uvicorn

    from orchestrator_v3 import V3Orchestrator
    from dashboard.app_v3 import create_v3_app

    md = "live" if live else "sim"
    port = port or settings.dashboard.port

    logger.info("=" * 55)
    logger.info("  TRADING PLATFORM v3.0 — Hybrid Architecture")
    logger.info("  Market data: %s | Dashboard: http://0.0.0.0:%d", md, port)
    logger.info("=" * 55)

    orch = V3Orchestrator(mode=md)

    if headless:
        asyncio.run(orch.start())
    else:
        def run_trading():
            asyncio.run(orch.start())

        t = threading.Thread(target=run_trading, daemon=True)
        t.start()
        time.sleep(1)

        app = create_v3_app(orchestrator=orch)
        logger.info("V3 Dashboard: http://0.0.0.0:%d", port)
        cfg = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()