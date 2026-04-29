import logging
from dataclasses import dataclass, field
from config.settings import settings
logger = logging.getLogger(__name__)

@dataclass
class RiskState:
    peak_equity: float = 0.0
    current_drawdown_pct: float = 0.0
    blocked_count: int = 0
    last_block_reason: str = ""

class RiskEngine:
    def __init__(self):
        self.cfg = settings.risk
        self.state = RiskState()
        self._halted = False

    def validate(self, signal, position, equity, entry_price=None):
        if signal == "HOLD": return "HOLD"
        if self._halted:
            self._record_block("Circuit breaker active"); return "BLOCK"
        self._update_drawdown(equity)
        if self.state.current_drawdown_pct > self.cfg.max_drawdown_pct:
            self._halt()
            self._record_block(f"Drawdown {self.state.current_drawdown_pct:.2%} exceeds {self.cfg.max_drawdown_pct:.2%}")
            return "BLOCK"
        # Position count check (0=no position, 1=long, -1=short)
        # execution.py handles BTC quantity sizing separately
        if position != 0:
            self._record_block(f"Position limit: already in position={position}")
            return "BLOCK"
        return signal

    def reset(self):
        self.state = RiskState(); self._halted = False
        logger.info("Risk engine reset")

    def _update_drawdown(self, equity):
        if equity > self.state.peak_equity: self.state.peak_equity = equity
        if self.state.peak_equity > 0:
            dd = (self.state.peak_equity - equity) / self.state.peak_equity
            self.state.current_drawdown_pct = max(dd, 0.0)

    def _halt(self):
        self._halted = True
        logger.critical("RISK HALT triggered - drawdown %.2f%%", self.state.current_drawdown_pct * 100)

    def _record_block(self, reason):
        self.state.blocked_count += 1; self.state.last_block_reason = reason
        logger.warning("Risk block #%d: %s", self.state.blocked_count, reason)