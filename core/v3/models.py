"""Typed event dataclasses for the v3 async event bus."""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, Optional


def _json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _json_ready(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_ready(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_json_ready(v) for v in value)
    return value


class EventType(Enum):
    TICK = "TICK"
    SIGNAL = "SIGNAL"
    FILL = "FILL"
    RISK_REJECTED = "RISK_REJECTED"
    PNL_SNAPSHOT = "PNL_SNAPSHOT"
    SYSTEM = "SYSTEM"


class Side(Enum):
    BUY = "BUY"
    SELL = "SELL"


class SignalSource(Enum):
    ML_MODEL = "ML_MODEL"
    RL_AGENT = "RL_AGENT"
    RULE_ENGINE = "RULE_ENGINE"


@dataclass
class BaseEvent:
    type: EventType = EventType.SYSTEM
    timestamp: float = field(default_factory=time.time)
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return _json_ready(asdict(self))


@dataclass
class TickEvent(BaseEvent):
    symbol: str = ""
    price: float = 0.0
    volume: int = 0
    bid: float = 0.0
    ask: float = 0.0

    def __post_init__(self) -> None:
        self.type = EventType.TICK
        self.source = "market_data"


@dataclass
class SignalEvent(BaseEvent):
    symbol: str = ""
    side: Side = Side.BUY
    price: float = 0.0
    strength: float = 0.0
    source: SignalSource = SignalSource.RULE_ENGINE
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.type = EventType.SIGNAL


@dataclass
class FillEvent(BaseEvent):
    fill_id: str = ""
    order_id: str = ""
    symbol: str = ""
    side: Side = Side.BUY
    quantity: float = 0.0
    price: float = 0.0
    commission: float = 0.0

    def __post_init__(self) -> None:
        self.type = EventType.FILL
        self.source = "broker"


@dataclass
class RiskRejectedEvent(BaseEvent):
    order_id: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        self.type = EventType.RISK_REJECTED
        self.source = "risk_manager"


@dataclass
class PnLSnapshot(BaseEvent):
    symbol: str = ""
    position: float = 0.0
    avg_entry: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    total_pnl: float = 0.0
    equity: float = 0.0

    def __post_init__(self) -> None:
        self.type = EventType.PNL_SNAPSHOT
        self.source = "portfolio"
