"""
v3 Core - Data Models and Event Schemas
========================================
Typed event objects for the async event bus pipeline.
These complement (not replace) the existing v1/v2 data models.

v3.2 additions:
  - RegimeType, PositionState enums
  - SignalEvent.score field (0-100)
  - RegimeSnapshot, RegimeChangeEvent
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


# ─── Enums ────────────────────────────────────────────────────────

class EventType(str, Enum):
    TICK = "TICK"
    SIGNAL = "SIGNAL"
    ORDER = "ORDER"
    FILL = "FILL"
    RISK_REJECTED = "RISK_REJECTED"
    PNL_SNAPSHOT = "PNL_SNAPSHOT"
    SYSTEM = "SYSTEM"
    REGIME_CHANGE = "REGIME_CHANGE"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class SignalSource(str, Enum):
    ML_MODEL = "ML_MODEL"
    RL_AGENT = "RL_AGENT"
    RULE_ENGINE = "RULE_ENGINE"


class RegimeType(str, Enum):
    """Market regime classification."""
    TREND = "TREND"
    RANGE = "RANGE"
    VOLATILE = "VOLATILE"


class PositionState(str, Enum):
    """Position lifecycle states for the state machine."""
    FLAT = "FLAT"
    ENTERING = "ENTERING"
    ACTIVE = "ACTIVE"
    EXIT = "EXIT"
    COOLDOWN = "COOLDOWN"


# ─── Base Event ───────────────────────────────────────────────────

@dataclass
class BaseEvent:
    type: EventType = EventType.SYSTEM
    timestamp: float = field(default_factory=time.time)
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        import dataclasses
        return dataclasses.asdict(self)


# ─── Market Events ────────────────────────────────────────────────

@dataclass
class TickEvent(BaseEvent):
    symbol: str = ""
    price: float = 0.0
    volume: int = 0
    bid: float = 0.0
    ask: float = 0.0
    exchange: str = ""

    def __post_init__(self):
        self.type = EventType.TICK
        self.source = "market_data"


# ─── Regime Events ────────────────────────────────────────────────

@dataclass
class RegimeSnapshot(BaseEvent):
    """Current market regime classification with metrics."""
    symbol: str = ""
    regime: RegimeType = RegimeType.RANGE
    atr: float = 0.0                    # Average True Range
    atr_pct: float = 0.0                 # ATR as % of price
    trend_strength: float = 0.0          # 0-1, how directional is the market
    volatility_percentile: float = 0.0   # 0-1, where current vol sits vs history
    regime_confidence: float = 0.0       # 0-1, how confident in classification
    prev_regime: RegimeType = RegimeType.RANGE
    consecutive_regime_bars: int = 0     # how long regime has been stable

    def __post_init__(self):
        self.type = EventType.REGIME_CHANGE
        self.source = "regime_classifier"


# ─── Signal Events ────────────────────────────────────────────────

@dataclass
class SignalEvent(BaseEvent):
    symbol: str = ""
    side: Side = Side.BUY
    price: float = 0.0
    strength: float = 1.0
    score: float = 0.0                   # v3.2: 0-100 composite signal score
    source: SignalSource = SignalSource.RULE_ENGINE
    regime: str = ""                     # v3.2: regime at signal time
    metadata: dict = field(default_factory=dict)

    def __post_init__(self):
        self.type = EventType.SIGNAL
        self.source = self.source.value if isinstance(self.source, SignalSource) else self.source


# ─── Execution Events ─────────────────────────────────────────────

@dataclass
class FillEvent(BaseEvent):
    fill_id: str = ""
    order_id: str = ""
    symbol: str = ""
    side: Side = Side.BUY
    quantity: int = 1
    price: float = 0.0
    commission: float = 0.0

    def __post_init__(self):
        self.type = EventType.FILL
        self.source = "execution"


@dataclass
class RiskRejectedEvent(BaseEvent):
    order_id: str = ""
    reason: str = ""

    def __post_init__(self):
        self.type = EventType.RISK_REJECTED
        self.source = "execution"


# ─── Analytics Events ─────────────────────────────────────────────

@dataclass
class PnLSnapshot(BaseEvent):
    symbol: str = ""
    position: int = 0
    avg_entry: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    total_pnl: float = 0.0
    equity: float = 0.0

    def __post_init__(self):
        self.type = EventType.PNL_SNAPSHOT
        self.source = "analytics"
