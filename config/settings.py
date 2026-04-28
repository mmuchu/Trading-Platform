"""Configuration settings for the Trading Platform.

v3.2: Added regime classifier, position FSM, signal scoring, and
      hard risk gating parameters.
"""
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
    # Drawdown & equity
    max_drawdown_pct: float = 0.05           # 5% max drawdown
    equity_floor_pct: float = 0.50           # 50% of initial = kill zone
    risk_per_trade_pct: float = 0.01         # 1% of equity per trade (legacy)
    risk_per_trade_pct_min: float = 0.005    # 0.5% min risk per trade
    risk_per_trade_pct_max: float = 0.01     # 1% max risk per trade

    # Position limits
    max_position_size: float = 0.1           # 0.1 BTC max position
    cash_reserve_pct: float = 0.30           # 30% cash reserve ($3K on $10K)
    min_position_size: float = 0.001         # 0.001 BTC minimum

    # SL/TP
    stop_loss_pct: float = 0.001             # 1.5% stop loss
    take_profit_pct: float = 0.002            # 3% take profit

    # Circuit breaker
    circuit_breaker_losses: int = 3          # consecutive losses to trigger
    circuit_breaker_cooldown_sec: float = 60.0


@dataclass
class BrokerConfig:
    initial_cash: float = 10000.0
    commission_pct: float = 0.001            # 0.1% commission
    slippage_pct: float = 0.0005             # 0.05% slippage


@dataclass
class StrategyConfig:
    momentum_lookback: int = 20
    momentum_threshold_pct: float = 0.3      # 0.3% minimum move to signal
    momentum_min_strength: float = 0.25     # minimum signal strength
    signal_cooldown_sec: float = 10.0        # cooldown between signals (legacy)
    signal_cooldown_ticks: int = 10          # tick-based cooldown
    breakout_window: int = 20
    breakout_threshold_pct: float = 0.02
    # Strategy calibration thresholds
    min_trade_score: float = 60.0
    strong_trade_score: float = 70.0
    notrade_zone_max: float = 55.0
    observe_zone_max: float = 65.0
    strong_score_to_execute: bool = True


@dataclass
class RegimeConfig:
    atr_period: int = 14
    trend_lookback: int = 30
    trend_strength_period: int = 20
    volatility_history: int = 100
    trend_adx_threshold: float = 0.20     # lower for tick-level data (was 0.35 for candle data)
    volatile_percentile: float = 0.80
    min_bars: int = 20
    regime_stability_bars: int = 10        # faster regime adaptation (was 5)


@dataclass
class SignalScoreConfig:
    momentum_weight: float = 0.40
    regime_weight: float = 0.25
    strength_weight: float = 0.15
    trend_weight: float = 0.20
    min_score_to_emit: float = 60.0
    volatile_regime_penalty: float = -30
    range_regime_penalty: float = -15


@dataclass
class PositionFSMConfig:
    entering_timeout_sec: float = 10.0
    exit_timeout_sec: float = 10.0
    cooldown_sec: float = 5.0
    max_hold_time_sec: float = 3600.0
    min_hold_time_sec: float = 2.0


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
    regime: RegimeConfig = field(default_factory=RegimeConfig)
    signal_score: SignalScoreConfig = field(default_factory=SignalScoreConfig)
    position_fsm: PositionFSMConfig = field(default_factory=PositionFSMConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    allocator: AllocatorConfig = field(default_factory=AllocatorConfig)
    order_router: OrderRouterConfig = field(default_factory=OrderRouterConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)
    mode: str = "PAPER"


settings = Settings()
