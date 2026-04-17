# Trading Platform

A complete quantitative trading platform with live Binance paper trading, backtesting, risk management, and a real-time dashboard.

## Features

- **Live Trading** - Real-time Binance WebSocket price feed with paper trading
- **Backtesting** - Deterministic engine with historical data from Binance
- **Risk Engine** - Max drawdown circuit breaker and position size limits
- **Strategies** - SMA Momentum and Channel Breakout
- **Execution** - Paper broker (simulated) and Binance live broker adapter
- **Dashboard** - FastAPI + WebSocket real-time UI with Chart.js equity curve
- **Analytics** - Sharpe ratio, Sortino ratio, profit factor, win rate

## Quick Start

```bash
pip install -r requirements.txt
python main.py --mode dashboard --port 8000
```

Open http://localhost:8000 in your browser.

## Modes

| Mode | Command | Description |
|------|---------|-------------|
| Dashboard | `python main.py --mode dashboard` | FastAPI web UI with WebSocket |
| Live | `python main.py --mode live` | Headless live paper trading |
| Backtest | `python main.py --mode backtest` | Run backtest with historical data |

## CLI Options

| Option | Default | Description |
|--------|---------|-------------|
| `--symbol` | BTCUSDT | Trading pair |
| `--strategy` | momentum | Strategy (momentum/breakout) |
| `--bars` | 500 | Number of bars for backtest |
| `--cash` | 10000 | Starting cash |
| `--port` | 8000 | Dashboard port |

## Project Structure

```
trading-platform/
  config/settings.py          # Configuration
  core/data/binance_ws.py     # WebSocket price feed
  core/data/historical_loader.px  # Historical data
  core/strategies/momentum.py     # SMA strategy
  core/strategies/breakout.py     # Breakout strategy
  core/risk/risk_engine.py        # Risk management
  core/execution/paper_broker.py  # Paper broker
  core/execution/binance_broker.py # Live broker
  core/engine/backtest.py         # Backtest engine
  core/engine/live.py             # Live trading loop
  core/analytics/pnl.px           # PnL tracking
  core/analytics/metrics.py       # Performance metrics
  dashboard/app.py                # FastAPI app
  dashboard/templates/index.html  # Web UI
  main.py                         # CLI entry point
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/status` | Engine status |
| POST | `/api/start` | Start live trading |
| POST | `/api/stop` | Stop trading |
| POST | `/api/reset` | Reset engine |
| GET | `/api/trades` | Trade history |
| GET | `/api/portfolio` | Portfolio summary |
| POST | `/api/backtest` | Run backtest |
| WS | `/ws` | Real-time updates |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `BINANCE_API_KEY` | Binance API key |
| `BINANCE_API_SECRET` | Binance API secret |
| `BINANCE_TESTNET` | Set to `true` for testnet |

## License

MIT
