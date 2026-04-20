import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class SignalType(Enum):
    BUY = "buy"
    SELL = "sell"
    CLOSE = "close"
    CLOSE_LONG = "close_long"
    CLOSE_SHORT = "close_short"
    HOLD = "hold"


class SignalSource(Enum):
    STRATEGY = "strategy"
    MANUAL = "manual"
    EXTERNAL = "external"
    SYSTEM = "system"


class SignalStatus(Enum):
    PENDING = "pending"
    VALIDATED = "validated"
    REJECTED = "rejected"
    EXECUTED = "executed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass
class TradingSignal:
    signal_id: str
    signal_type: SignalType
    symbol: str
    source: SignalSource
    strength: float = 0.5
    entry_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    quantity: float = 0.0
    timeframe: str = ""
    strategy_name: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""
    status: SignalStatus = SignalStatus.PENDING
    rejection_reason: str = ""
    ttl_seconds: int = 300

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()
        if not self.signal_id:
            self.signal_id = f"SIG-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"


class SignalProcessor:

    def __init__(self, max_pending: int = 50, default_ttl: int = 300):
        self._signals: Dict[str, TradingSignal] = {}
        self._history: List[TradingSignal] = []
        self._max_pending = max_pending
        self._default_ttl = default_ttl
        self._counter = 0
        self._filters: List[callable] = []
        logger.info(f"SignalProcessor initialized (max_pending={max_pending}, ttl={default_ttl}s)")

    def add_filter(self, filter_fn: callable):
        self._filters.append(filter_fn)

    def create_signal(self, signal_type: SignalType, symbol: str, source: SignalSource = SignalSource.STRATEGY, strength: float = 0.5, entry_price: float = 0.0, stop_loss: float = 0.0, take_profit: float = 0.0, quantity: float = 0.0, strategy_name: str = "", metadata: Dict = None, ttl: int = 0) -> TradingSignal:
        pending = [s for s in self._signals.values() if s.status == SignalStatus.PENDING]
        if len(pending) >= self._max_pending:
            logger.warning(f"Max pending signals ({self._max_pending}) reached. Oldest signal rejected.")
            oldest = min(pending, key=lambda s: s.timestamp)
            oldest.status = SignalStatus.REJECTED
            oldest.rejection_reason = "Max pending limit"
        self._counter += 1
        signal = TradingSignal(
            signal_id=f"SIG-{self._counter:06d}",
            signal_type=signal_type, symbol=symbol, source=source,
            strength=max(0.0, min(1.0, strength)),
            entry_price=entry_price, stop_loss=stop_loss, take_profit=take_profit,
            quantity=quantity, strategy_name=strategy_name,
            metadata=metadata or {}, ttl_seconds=ttl or self._default_ttl,
        )
        self._signals[signal.signal_id] = signal
        self._history.append(signal)
        logger.info(f"Signal created: {signal.signal_id} {signal_type.value} {symbol} strength={strength:.2f}")
        return signal

    def validate_signal(self, signal_id: str) -> Tuple[bool, str]:
        signal = self._signals.get(signal_id)
        if not signal:
            return False, "Signal not found"
        if signal.status != SignalStatus.PENDING:
            return False, f"Signal is {signal.status.value}"
        if self._is_expired(signal):
            signal.status = SignalStatus.EXPIRED
            return False, "Signal expired"
        for filter_fn in self._filters:
            try:
                passed, reason = filter_fn(signal)
                if not passed:
                    signal.status = SignalStatus.REJECTED
                    signal.rejection_reason = reason
                    logger.info(f"Signal {signal_id} rejected by filter: {reason}")
                    return False, reason
            except Exception as e:
                logger.error(f"Filter error on {signal_id}: {e}")
        signal.status = SignalStatus.VALIDATED
        logger.info(f"Signal {signal_id} validated")
        return True, "OK"

    def mark_executed(self, signal_id: str):
        signal = self._signals.get(signal_id)
        if signal:
            signal.status = SignalStatus.EXECUTED

    def mark_cancelled(self, signal_id: str, reason: str = ""):
        signal = self._signals.get(signal_id)
        if signal:
            signal.status = SignalStatus.CANCELLED
            signal.rejection_reason = reason

    def get_pending_signals(self) -> List[TradingSignal]:
        now = datetime.now()
        pending = []
        for s in self._signals.values():
            if s.status == SignalStatus.PENDING:
                if self._is_expired(s):
                    s.status = SignalStatus.EXPIRED
                else:
                    pending.append(s)
        return sorted(pending, key=lambda s: s.strength, reverse=True)

    def get_signal(self, signal_id: str) -> Optional[TradingSignal]:
        return self._signals.get(signal_id)

    def get_signals_by_symbol(self, symbol: str, status: Optional[SignalStatus] = None) -> List[TradingSignal]:
        signals = [s for s in self._signals.values() if s.symbol == symbol]
        if status:
            signals = [s for s in signals if s.status == status]
        return sorted(signals, key=lambda s: s.timestamp, reverse=True)

    def _is_expired(self, signal: TradingSignal) -> bool:
        if signal.ttl_seconds <= 0:
            return False
        try:
            created = datetime.fromisoformat(signal.timestamp)
            elapsed = (datetime.now() - created).total_seconds()
            return elapsed > signal.ttl_seconds
        except (ValueError, TypeError):
            return False

    def cleanup_expired(self) -> int:
        count = 0
        for s in list(self._signals.values()):
            if s.status == SignalStatus.PENDING and self._is_expired(s):
                s.status = SignalStatus.EXPIRED
                count += 1
        if count:
            logger.info(f"Cleaned up {count} expired signals")
        return count

    def get_stats(self) -> Dict:
        by_status = {}
        for s in self._signals.values():
            by_status[s.status.value] = by_status.get(s.status.value, 0) + 1
        return {"total_signals": len(self._signals), "by_status": by_status,
                "pending": len(self.get_pending_signals()), "filters": len(self._filters)}

    def get_status(self) -> Dict:
        return self.get_stats()
