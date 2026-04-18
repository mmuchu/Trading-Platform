"""Execution Analytics - post-trade quality measurement."""
import logging, time
from dataclasses import dataclass, field
from typing import Dict, List
from core.execution.order import ExecutionReport
logger = logging.getLogger(__name__)
@dataclass
class AlgorithmStats:
    algorithm: str
    total_orders: int = 0
    completed_orders: int = 0
    cancelled_orders: int = 0
    avg_fill_pct: float = 0.0
    avg_slippage_bps: float = 0.0
    avg_duration_secs: float = 0.0
    total_commission: float = 0.0
    total_notional: float = 0.0
    worst_slippage_bps: float = 0.0
    best_slippage_bps: float = 0.0
class ExecutionAnalytics:
    def __init__(self, max_history=1000):
        self._reports = []
        self._max_history = max_history
        self._algorithm_stats = {}
    def record(self, report):
        self._reports.append(report)
        if len(self._reports) > self._max_history:
            self._reports = self._reports[-self._max_history:]
        self._update_algorithm_stats(report)
    def get_summary(self):
        if not self._reports:
            return {"total_orders": 0}
        total = len(self._reports)
        completed = sum(1 for r in self._reports if r.is_complete)
        cancelled = sum(1 for r in self._reports if r.status == "CANCELLED")
        avg_fill = sum(r.fill_pct for r in self._reports) / total
        avg_slip = sum(r.total_slippage_bps for r in self._reports) / total
        total_notional = sum(r.total_notional for r in self._reports)
        total_comm = sum(r.total_commission for r in self._reports)
        durations = [r.duration_secs for r in self._reports if r.duration_secs > 0]
        avg_dur = sum(durations) / max(len(durations), 1)
        return {"total_orders": total, "completed_orders": completed,
            "cancelled_orders": cancelled,
            "completion_rate_pct": round(completed / max(total, 1) * 100, 2),
            "avg_fill_pct": round(avg_fill, 2),
            "avg_slippage_bps": round(avg_slip, 2),
            "total_notional": round(total_notional, 2),
            "total_commission": round(total_comm, 4),
            "total_cost_bps": round((total_comm / total_notional * 10000) if total_notional > 0 else 0, 2),
            "avg_duration_secs": round(avg_dur, 2),
            "algorithm_breakdown": self._get_algorithm_breakdown()}
    def get_algorithm_comparison(self):
        result = []
        for algo, stats in sorted(self._algorithm_stats.items()):
            result.append({"algorithm": algo, "total_orders": stats.total_orders,
                "completion_rate_pct": round(stats.completed_orders / max(stats.total_orders, 1) * 100, 2),
                "avg_fill_pct": round(stats.avg_fill_pct, 2),
                "avg_slippage_bps": round(stats.avg_slippage_bps, 2),
                "best_slippage_bps": round(stats.best_slippage_bps, 2),
                "worst_slippage_bps": round(stats.worst_slippage_bps, 2),
                "avg_duration_secs": round(stats.avg_duration_secs, 2),
                "total_commission": round(stats.total_commission, 4),
                "total_notional": round(stats.total_notional, 2)})
        return result
    def get_recent_reports(self, limit=10):
        return [r.to_dict() for r in reversed(self._reports[-limit:])]
    def get_slippage_distribution(self):
        if not self._reports:
            return {}
        slippages = sorted(r.total_slippage_bps for r in self._reports)
        n = len(slippages)
        mean = sum(slippages) / n
        return {"mean": round(mean, 2), "median": round(slippages[n // 2], 2),
            "p10": round(slippages[max(0, n // 10)], 2),
            "p90": round(slippages[min(n - 1, 9 * n // 10)], 2),
            "min": round(slippages[0], 2), "max": round(slippages[-1], 2),
            "std": round((sum((s - mean) ** 2 for s in slippages) / n) ** 0.5, 2)}
    def _update_algorithm_stats(self, report):
        algo = report.algorithm
        if algo not in self._algorithm_stats:
            self._algorithm_stats[algo] = AlgorithmStats(algorithm=algo)
        stats = self._algorithm_stats[algo]
        stats.total_orders += 1
        if report.is_complete:
            stats.completed_orders += 1
        if report.status == "CANCELLED":
            stats.cancelled_orders += 1
        n = stats.total_orders
        stats.avg_fill_pct += (report.fill_pct - stats.avg_fill_pct) / n
        stats.avg_slippage_bps += (report.total_slippage_bps - stats.avg_slippage_bps) / n
        if report.duration_secs > 0:
            stats.avg_duration_secs += (report.duration_secs - stats.avg_duration_secs) / n
        stats.total_commission += report.total_commission
        stats.total_notional += report.total_notional
        if stats.worst_slippage_bps == 0 or report.total_slippage_bps > stats.worst_slippage_bps:
            stats.worst_slippage_bps = report.total_slippage_bps
        if stats.best_slippage_bps == 0 or report.total_slippage_bps < stats.best_slippage_bps:
            stats.best_slippage_bps = report.total_slippage_bps
    def _get_algorithm_breakdown(self):
        bd = {}
        for r in self._reports:
            bd[r.algorithm] = bd.get(r.algorithm, 0) + 1
        return bd
    def reset(self):
        self._reports.clear()
        self._algorithm_stats.clear()
