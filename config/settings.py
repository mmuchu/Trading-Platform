"""Configuration settings for the Trading Platform."""
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class BinanceConfig:
    ws_base: str = "wss://stream.binance.com:9443/ws"
    rest_base: str = "https://api.binance.com/api/v3"
    symbol: str = "btcusdt"
    trade_ws_url: str = field(init=False)
    kline_ws_url: str = field(init=False)
    def __post_init__(self):
        self.trade_ws_url = f"{self.ws_base}/{self.symbol}@trade"
        self.kline_ws_url = f"{self.ws_base}/{self.symbol}@kline_1m"

@dataclass
class RiskConfig:
    max_drawdown_pct: float = 0.05
    max_position_size: int = 3
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.04
    risk_per_trade_pct: float = 0.01

@dataclass
class BrokerConfig:
    initial_cash: float = 10000.0
    commission_pct: float = 0.001
    slippage_pct: float = 0.0005

@dataclass
class StrategyConfig:
    momentum_lookback: int = 5
    breakout_window: int = 20
    breakout_threshold_pct: float = 0.02

@dataclass
class BacktestConfig:
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    initial_cash: float = 10000.0
    bar_interval: str = "1m"

@dataclass
class AllocatorConfig:
    max_ratio: float = 2.0
    min_mm_inventory: float = 1000.0
    correlation_window: int = 100
    max_hedge_pct: float = 0.5
    exposure_check_interval: int = 1

@dataclass
class OrderRouterConfig:
    """Smart Order Router configuration."""
    default_algorithm: str = "slice"
    max_child_orders: int = 10
    twap_duration_secs: float = 300.0
    twap_interval_secs: float = 30.0
    vwap_duration_secs: float = 600.0
    participation_rate_max: float = 0.1
    cancel_on_drawdown_pct: float = 0.5

@dataclass
class DashboardConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = True

@dataclass
class Settings:
    binance: BinanceConfig = field(default_factory=BinanceConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    broker: BrokerConfig = field(default_factory=BrokerConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    allocator: AllocatorConfig = field(default_factory=AllocatorConfig)
    order_router: OrderRouterConfig = field(default_factory=OrderRouterConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    mode: str = "PAPER"

settings = Settings()
