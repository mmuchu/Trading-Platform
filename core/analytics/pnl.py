import logging
from dataclasses import dataclass
logger = logging.getLogger(__name__)

@dataclass
class PnlSnapshot:
    timestamp: float; equity: float; cash: float; position_value: float; unrealised_pnl: float; realised_pnl: float; total_pnl: float; drawdown_pct: float

class PnlTracker:
    def __init__(self, initial_cash):
        self.initial_cash = initial_cash; self.snapshots = []; self._peak = initial_cash; self._eq_hist = [initial_cash]

    def record(self, timestamp, equity, cash, position, current_price, realised_pnl):
        pv = position * current_price; total_pnl = equity - self.initial_cash
        if equity > self._peak: self._peak = equity
        dd = max((self._peak-equity)/self._peak, 0.0) if self._peak > 0 else 0.0
        s = PnlSnapshot(timestamp=timestamp,equity=round(equity,4),cash=round(cash,4),position_value=round(pv,4),unrealised_pnl=0,realised_pnl=round(realised_pnl,4),total_pnl=round(total_pnl,4),drawdown_pct=round(dd,6))
        self.snapshots.append(s); self._eq_hist.append(equity); return s

    def max_drawdown(self): return max((s.drawdown_pct for s in self.snapshots), default=0.0)
    def equity_series(self): return list(self._eq_hist)
    def total_return(self):
        if not self._eq_hist: return 0.0
        return (self._eq_hist[-1]-self.initial_cash)/self.initial_cash

    def summary(self):
        eq = self._eq_hist
        return {"initial_cash":self.initial_cash,"final_equity":eq[-1] if eq else self.initial_cash,"total_pnl":(eq[-1]-self.initial_cash) if eq else 0,"total_return_pct":round(self.total_return()*100,2),"max_drawdown_pct":round(self.max_drawdown()*100,2),"snapshots_count":len(self.snapshots)}

    def reset(self):
        self.snapshots.clear(); self._peak=self.initial_cash; self._eq_hist=[self.initial_cash]