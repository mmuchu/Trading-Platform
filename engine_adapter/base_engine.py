import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from enum import Enum
from datetime import datetime

logger = logging.getLogger(__name__)


class EngineType(Enum):
    PAPER = "paper"
    CCXT = "ccxt"
    CUSTOM = "custom"


class ConnectionStatus(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


@dataclass
class EngineConfig:
    engine_type: EngineType = EngineType.PAPER
    api_key: str = ""
    api_secret: str = ""
    passphrase: str = ""
    sandbox: bool = True
    rate_limit_ms: int = 200
    timeout_seconds: int = 30
    max_retries: int = 3
    retry_delay_ms: int = 1000
    default_symbol: str = "BTC/USDT"
    exchange_id: str = "binance"
    testnet_url: str = ""
    mainnet_url: str = ""


@dataclass
class Balance:
    asset: str
    free: float
    used: float
    total: float

    def __post_init__(self):
        self.free = round(self.free, 8)
        self.used = round(self.used, 8)
        self.total = round(self.total, 8)


@dataclass
class Ticker:
    symbol: str
    bid: float
    ask: float
    last: float
    high: float
    low: float
    volume: float
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()

    @property
    def mid(self) -> float:
        return round((self.bid + self.ask) / 2, 8)

    @property
    def spread(self) -> float:
        return round(self.ask - self.bid, 8)

    @property
    def spread_pct(self) -> float:
        return round(self.spread / self.ask * 100, 6) if self.ask > 0 else 0


@dataclass
class OrderBookEntry:
    price: float
    quantity: float


@dataclass
class OrderBook:
    symbol: str
    bids: List[OrderBookEntry]
    asks: List[OrderBookEntry]
    timestamp: str = ""


class BaseEngine(ABC):

    def __init__(self, config: EngineConfig):
        self.config = config
        self._status = ConnectionStatus.DISCONNECTED
        self._tickers: Dict[str, Ticker] = {}
        self._balances: Dict[str, Balance] = {}
        self._open_orders: Dict[str, Dict] = {}
        self.logger = logging.getLogger(f"{self.__class__.__name__}")
        self.logger.info(f"Engine initialized: {config.engine_type.value}")

    @abstractmethod
    def connect(self) -> bool:
        pass

    @abstractmethod
    def disconnect(self) -> bool:
        pass

    @abstractmethod
    def fetch_ticker(self, symbol: str) -> Optional[Ticker]:
        pass

    @abstractmethod
    def fetch_balance(self) -> Dict[str, Balance]:
        pass

    @abstractmethod
    def fetch_order_book(self, symbol: str, limit: int = 20) -> Optional[OrderBook]:
        pass

    @abstractmethod
    def create_market_order(self, symbol: str, side: str, quantity: float) -> Optional[Dict]:
        pass

    @abstractmethod
    def create_limit_order(self, symbol: str, side: str, quantity: float, price: float) -> Optional[Dict]:
        pass

    @abstractmethod
    def cancel_order(self, order_id: str, symbol: str) -> bool:
        pass

    @abstractmethod
    def fetch_open_orders(self, symbol: str = "") -> List[Dict]:
        pass

    @abstractmethod
    def fetch_my_trades(self, symbol: str = "", limit: int = 50) -> List[Dict]:
        pass

    def get_status(self) -> ConnectionStatus:
        return self._status

    def get_ticker(self, symbol: str) -> Optional[Ticker]:
        return self._tickers.get(symbol)

    def get_all_tickers(self) -> Dict[str, Ticker]:
        return dict(self._tickers)

    def get_balance(self, asset: str) -> Optional[Balance]:
        return self._balances.get(asset)

    def get_all_balances(self) -> Dict[str, Balance]:
        return dict(self._balances)

    @property
    def is_connected(self) -> bool:
        return self._status == ConnectionStatus.CONNECTED

    def format_symbol(self, symbol: str) -> str:
        return symbol.replace("_", "/").replace("-", "/").upper()

    def parse_symbol(self, symbol: str) -> str:
        return symbol.replace("/", "_").replace("-", "_").lower()
