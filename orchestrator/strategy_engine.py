import logging
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class StrategyType(Enum):
    MOMENTUM = "momentum"
    MEAN_REVERSION = "mean_reversion"
    TREND_FOLLOWING = "trend_following"
    BREAKOUT = "breakout"
    SCALPING = "scalping"
    GRID = "grid"
    DCA = "dollar_cost_averaging"
    CUSTOM = "custom"


class StrategyState(Enum):
    DISABLED = "disabled"
    IDLE = "idle"
    ANALYZING = "analyzing"
    SIGNAL_GENERATED = "signal_generated"
    PAUSED = "paused"


@dataclass
class StrategyConfig:
    name: str
    strategy_type: StrategyType
    enabled: bool = True
    symbols: List[str] = field(default_factory=list)
    timeframe: str = "1h"
    parameters: Dict[str, Any] = field(default_factory=dict)
    max_position_per_symbol: int = 1
    cooldown_seconds: int = 60
    min_signal_strength: float = 0.3


@dataclass
class StrategyResult:
    strategy_name: str
    signals: List[Dict] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)
    state: StrategyState = StrategyState.IDLE
    timestamp: str = ""


class StrategyBase:

    def __init__(self, config: StrategyConfig):
        self.config = config
        self.state = StrategyState.IDLE
        self._last_signal_time: Optional[datetime] = None
        self._signal_count = 0
        logger.info(f"Strategy '{config.name}' initialized ({config.strategy_type.value})")

    def on_bar(self, candles: Dict[str, List[Dict]], market_data: Dict = None) -> StrategyResult:
        raise NotImplementedError

    def can_trade(self, symbol: str) -> bool:
        if self._last_signal_time:
            elapsed = (datetime.now() - self._last_signal_time).total_seconds()
            if elapsed < self.config.cooldown_seconds:
                return False
        return self.config.enabled and self.state not in (StrategyState.DISABLED, StrategyState.PAUSED)

    def _record_signal(self):
        self._last_signal_time = datetime.now()
        self._signal_count += 1


class StrategyEngine:

    def __init__(self):
        self._strategies: Dict[str, StrategyBase] = {}
        self._strategy_configs: Dict[str, StrategyConfig] = {}
        self._results_history: List[StrategyResult] = []
        self._data_callback: Optional[Callable] = None
        self._signal_callback: Optional[Callable] = None
        logger.info("StrategyEngine initialized")

    def set_data_callback(self, callback: Callable):
        self._data_callback = callback

    def set_signal_callback(self, callback: Callable):
        self._signal_callback = callback

    def register_strategy(self, config: StrategyConfig, strategy_instance: StrategyBase = None):
        if strategy_instance:
            self._strategies[config.name] = strategy_instance
        else:
            self._strategies[config.name] = StrategyBase(config)
        self._strategy_configs[config.name] = config
        logger.info(f"Strategy registered: {config.name} ({config.strategy_type.value})")

    def unregister_strategy(self, name: str) -> bool:
        if name in self._strategies:
            del self._strategies[name]
            self._strategy_configs.pop(name, None)
            return True
        return False

    def enable_strategy(self, name: str):
        config = self._strategy_configs.get(name)
        if config:
            config.enabled = True
            logger.info(f"Strategy '{name}' enabled")

    def disable_strategy(self, name: str):
        config = self._strategy_configs.get(name)
        if config:
            config.enabled = False
            logger.info(f"Strategy '{name}' disabled")

    def run_all(self, candles: Dict[str, List[Dict]], market_data: Dict = None) -> List[StrategyResult]:
        results = []
        for name, strategy in self._strategies.items():
            config = self._strategy_configs.get(name)
            if not config or not config.enabled:
                continue
            if config.symbols:
                filtered = {s: c for s, c in candles.items() if s in config.symbols}
            else:
                filtered = candles
            if not filtered:
                continue
            try:
                strategy.state = StrategyState.ANALYZING
                result = strategy.on_bar(filtered, market_data)
                strategy.state = StrategyState.SIGNAL_GENERATED if result.signals else StrategyState.IDLE
                result.timestamp = datetime.now().isoformat()
                results.append(result)
                self._results_history.append(result)
                if result.signals and self._signal_callback:
                    for sig in result.signals:
                        sig["strategy_name"] = name
                        self._signal_callback(sig)
            except Exception as e:
                logger.error(f"Strategy '{name}' error: {e}")
                strategy.state = StrategyState.IDLE
        if len(self._results_history) > 500:
            self._results_history = self._results_history[-500:]
        return results

    def get_strategy(self, name: str) -> Optional[StrategyBase]:
        return self._strategies.get(name)

    def get_config(self, name: str) -> Optional[StrategyConfig]:
        return self._strategy_configs.get(name)

    def get_all_configs(self) -> Dict[str, StrategyConfig]:
        return dict(self._strategy_configs)

    def update_config(self, name: str, **kwargs):
        config = self._strategy_configs.get(name)
        if config:
            for k, v in kwargs.items():
                if hasattr(config, k):
                    setattr(config, k, v)

    def get_status(self) -> Dict:
        configs = self._strategy_configs
        active = sum(1 for c in configs.values() if c.enabled)
        return {
            "total_strategies": len(self._strategies),
            "active_strategies": active,
            "strategies": {
                name: {"type": c.strategy_type.value, "enabled": c.enabled,
                       "symbols": c.symbols, "state": self._strategies[name].state.value if name in self._strategies else "unknown"}
                for name, c in configs.items()
            },
            "total_results": len(self._results_history),
        }
