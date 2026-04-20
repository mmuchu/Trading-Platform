"""
Risk Guard Module — 5-gate pre-trade risk controller.

Gates:
  1. Feed Monitor   — Is the market data feed alive and recent?
  2. Signal Validator — Is the signal structurally valid and sensible?
  3. Risk Checker   — Does the trade pass drawdown, exposure, and VaR limits?
  4. Position Sync  — Is position state consistent with the execution layer?
  5. Cooldown Manager — Has enough time passed since the last trade on this symbol?

The PortfolioRiskGuard orchestrates all 5 gates.  Any gate failure produces a
RiskRejectedEvent instead of a fill.

Usage::

    guard = PortfolioRiskGuard(bus, execution_service)
    await guard.start()

    # In the execution service's signal handler:
    result = await guard.evaluate(signal)
    if result.approved:
        fill = execute(signal)
    else:
        bus.publish(result.rejection_event)
"""

from risk_guard.feed_monitor import FeedMonitor
from risk_guard.signal_validator import SignalValidator
from risk_guard.risk_checker import RiskChecker
from risk_guard.position_sync import PositionSyncGuard
from risk_guard.cooldown_manager import CooldownManager
from risk_guard.sl_tp_manager import SLTPManager
from risk_guard.guard import PortfolioRiskGuard, GateResult

__all__ = [
    "PortfolioRiskGuard",
    "GateResult",
    "FeedMonitor",
    "SignalValidator",
    "RiskChecker",
    "PositionSyncGuard",
    "CooldownManager",
    "SLTPManager",
]