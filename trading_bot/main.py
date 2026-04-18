"""
Trading Bot v1.0 - Entry Point
================================
Usage:
    python -m trading_bot                  # Live paper trading (Binance)
    python -m trading_bot --mode sim        # Simulated data (local testing)
    python -m trading_bot --mode backtest   # Backtest with sim data
    python -m trading_bot --dashboard       # With web dashboard
    python -m trading_bot --mode sim --dashboard  # Sim + dashboard
"""

import argparse
import logging
import sys
import os

# Ensure the parent directory is on path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def setup_logging(level="INFO"):
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s  %(name)-20s  %(levelname)-5s  %(message)s",
        datefmt="%H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def parse_args():
    p = argparse.ArgumentParser(
        prog="trading_bot",
        description="Trading Bot v1.0 - Production Baseline"
    )
    p.add_argument(
        "--mode", choices=["live", "sim", "backtest"], default="sim",
        help="Data source: live=Binance REST API, sim=simulated, backtest=pre-generated"
    )
    p.add_argument(
        "--dashboard", action="store_true",
        help="Start web dashboard on port 8080"
    )
    p.add_argument(
        "--port", type=int, default=8080,
        help="Dashboard port (default: 8080)"
    )
    p.add_argument(
        "--bars", type=int, default=500,
        help="Number of bars for backtest (default: 500)"
    )
    p.add_argument(
        "--symbol", type=str, default="BTCUSDT",
        help="Trading symbol (default: BTCUSDT)"
    )
    p.add_argument(
        "--cash", type=float, default=10000,
        help="Initial cash (default: 10000)"
    )
    p.add_argument(
        "--max-pos", type=int, default=3,
        help="Max position size (default: 3)"
    )
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    return p.parse_args()


def main():
    args = parse_args()
    setup_logging(args.log_level)

    from trading_bot.config import Config
    from trading_bot.core.engine import TradingEngine

    config = Config()
    config.SYMBOL = args.symbol
    config.INITIAL_CASH = args.cash
    config.MAX_POSITION = args.max_pos
    config.DASHBOARD_PORT = args.port
    config.BACKTEST_BARS = args.bars

    logger = logging.getLogger("main")
    logger.info("Trading Bot v1.0 starting...")
    logger.info("Mode: %s | Symbol: %s | Cash: $%.0f", args.mode, args.symbol, args.cash)

    engine = TradingEngine(config=config, mode=args.mode)

    if args.dashboard:
        try:
            from trading_bot.dashboard.app import run_dashboard
            run_dashboard(engine, port=args.port)
            logger.info("Dashboard: http://localhost:%d", args.port)
        except ImportError:
            logger.error("Dashboard requires fastapi and uvicorn.")
            logger.error("Install: pip install fastapi uvicorn")
            logger.info("Continuing without dashboard...")

    try:
        engine.run()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        engine.store.close()


if __name__ == "__main__":
    main()
