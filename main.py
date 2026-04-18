"""
Trading Platform - Main Entry Point
====================================
  python main.py --mode dashboard   # v1.0 (port 8000)
  python main.py --mode v3          # v3.0 (port 8001)
  python main.py --mode v3.1        # v3.1 Edge Optimized (port 8002)
"""

from __future__ import annotations

import argparse, asyncio, logging, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def setup_logging(level="INFO"):
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s  %(name)-30s  %(levelname)-7s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S", handlers=[logging.StreamHandler(sys.stdout)])
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

def parse_args():
    p = argparse.ArgumentParser(description="Trading Platform v1.0 + v3.0 + v3.1")
    p.add_argument("--mode", choices=["dashboard", "live", "backtest", "v3", "v3.1"], default="dashboard")
    p.add_argument("--strategy", choices=["momentum", "breakout"], default="momentum")
    p.add_argument("--symbol", default="btcusdt")
    p.add_argument("--bars", type=int, default=1000)
    p.add_argument("--cash", type=float, default=10000)
    p.add_argument("--data", choices=["sim", "live"], default="sim", help="[v3/v3.1] data mode")
    p.add_argument("--no-dash", action="store_true", help="[v3/v3.1] no dashboard")
    p.add_argument("--port", type=int, default=8001, help="[v3/v3.1] dashboard port")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p.parse_args()

def run_v1(args):
    logger = logging.getLogger("main")
    logger.info("Trading Platform v1.0 | Mode: %s | Strategy: %s", args.mode.upper(), args.strategy)
    try:
        if args.mode == "dashboard":
            from core.engine.dashboard import DashboardEngine
            DashboardEngine(strategy=args.strategy, symbol=args.symbol, bars=args.bars, cash=args.cash).run(host="0.0.0.0", port=8000)
        elif args.mode == "live":
            from core.engine.live import LiveEngine
            LiveEngine(strategy=args.strategy, symbol=args.symbol, cash=args.cash).run()
        elif args.mode == "backtest":
            from core.engine.backtest import BacktestEngine
            results = BacktestEngine(strategy=args.strategy, symbol=args.symbol, bars=args.bars, cash=args.cash).run()
            print(f"Backtest: {results}")
    except ImportError as e:
        logger.error("v1.0 import error: %s", e)
        sys.exit(1)

async def run_v3(args):
    logger = logging.getLogger("v3")
    logger.info("=" * 50)
    logger.info("QUANT TRADING SYSTEM v3.0")
    logger.info("=" * 50)
    import uvicorn
    from v3 import Config, Orchestrator, create_dashboard
    config = Config()
    config.dash_port = args.port
    config.log_level = args.log_level
    orch = Orchestrator(config)
    orch.market_data.mode = args.data
    if args.no_dash:
        logger.info("Headless mode, data=%s", args.data)
        await orch.start()
    else:
        import threading
        threading.Thread(target=lambda: asyncio.run(orch.start()), daemon=True).start()
        await asyncio.sleep(1)
        logger.info("Dashboard: http://0.0.0.0:%d", args.port)
        await uvicorn.Server(uvicorn.Config(create_dashboard(orch), host="0.0.0.0",
            port=args.port, log_level=args.log_level.lower())).serve()

async def run_v3_1(args):
    logger = logging.getLogger("v3.1")
    logger.info("=" * 60)
    logger.info("QUANT TRADING SYSTEM v3.1 - Edge Optimized")
    logger.info("=" * 60)
    import uvicorn
    from v3_1 import Config, Orchestrator, create_dashboard
    config = Config()
    config.dash_port = args.port
    config.log_level = args.log_level
    orch = Orchestrator(config)
    orch.market_data.mode = args.data
    if args.no_dash:
        logger.info("Headless mode, data=%s", args.data)
        await orch.start()
    else:
        import threading
        threading.Thread(target=lambda: asyncio.run(orch.start()), daemon=True).start()
        await asyncio.sleep(1)
        logger.info("Dashboard: http://0.0.0.0:%d", args.port)
        await uvicorn.Server(uvicorn.Config(create_dashboard(orch), host="0.0.0.0",
            port=args.port, log_level=args.log_level.lower())).serve()

def main():
    args = parse_args()
    setup_logging(args.log_level)
    if args.mode == "v3":
        try: asyncio.run(run_v3(args))
        except KeyboardInterrupt: pass
    elif args.mode == "v3.1":
        try: asyncio.run(run_v3_1(args))
        except KeyboardInterrupt: pass
    else:
        try: run_v1(args)
        except KeyboardInterrupt: pass

if __name__ == "__main__":
    main()
