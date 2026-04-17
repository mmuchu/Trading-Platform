# Trading Platform

A fully functional quantitative trading platform (MVP) with live paper trading, backtesting, risk management, and a real-time WebSocket dashboard.

## Features

- **Live Trading** - Real-time Binance WebSocket price feed with paper trading simulation
- **Backtesting** - Deterministic replay engine using the same strategy/risk/execution pipeline
- **Risk Engine** - Max drawdown circuit breaker, position caps, per-trade validation
- **Strategies** - Momentum (SMA cross) and Breakout (channel) strategies with ML-ready hooks
- **Execution** - Paper broker (commission + slippage modeling) and Binance live broker (testnet-ready)
- **Analytics** - Sharpe ratio, Sortino ratio, profit factor, win rate, equity curve, drawdown tracking
- **Dashboard** - FastAPI + WebSocket real-time dark-themed UI with Chart.js equity chart and trade log
- **REST API** - Full control: start/stop/reset engine, fetch trades, portfolio, status, run backtests

## Quick Start

`ash
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\Activate.ps1
pip install -r requirements.txt

python main.py                    # Dashboard at http://localhost:8000
python main.py --mode live        # Live paper trading
python main.py --mode backtest    # Run backtest
python main.py --strategy breakout --symbol ethusdt
`

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | / | Dashboard UI |
| GET | /api/status | Engine status |
| POST | /api/start | Start engine |
| POST | /api/stop | Stop engine |
| POST | /api/reset | Reset engine |
| GET | /api/trades | All trades |
| GET | /api/portfolio | Portfolio |
| GET | /api/backtest | Run backtest |
| WS | /ws | Live tick stream |

## License

MIT
