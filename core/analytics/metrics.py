import math
import logging
logger = logging.getLogger(__name__)

class Metrics:
    @staticmethod
    def returns(eq):
        if len(eq)<2: return []
        return [(eq[i]-eq[i-1])/eq[i-1] for i in range(1,len(eq))]

    @staticmethod
    def sharpe_ratio(eq, rf=0.0, ppy=252):
        r = Metrics.returns(eq)
        if len(r)<2: return None
        m = sum(r)/len(r); v = sum((x-m)**2 for x in r)/len(r); s = math.sqrt(v)
        if s==0: return None
        return (m*ppy-rf)/(s*math.sqrt(ppy))

    @staticmethod
    def sortino_ratio(eq, rf=0.0, ppy=252):
        r = Metrics.returns(eq)
        if len(r)<2: return None
        m = sum(r)/len(r); down=[x for x in r if x<0]
        if not down: return float("inf")
        dv = sum(x**2 for x in down)/len(down); dd = math.sqrt(dv)
        am = m*ppy; add = dd*math.sqrt(ppy)
        return (am-rf)/add if add else None

    @staticmethod
    def max_drawdown(eq):
        peak=eq[0] if eq else 0; md=0.0
        for v in eq:
            if v>peak: peak=v
            dd=(peak-v)/peak if peak>0 else 0
            if dd>md: md=dd
        return md

    @staticmethod
    def profit_factor(trades):
        gp = sum(t["pnl"] for t in trades if t["pnl"]>0)
        gl = abs(sum(t["pnl"] for t in trades if t["pnl"]<0))
        if gl==0: return float("inf") if gp>0 else None
        return gp/gl

    @staticmethod
    def win_rate(trades):
        if not trades: return None
        return sum(1 for t in trades if t.get("pnl",0)>0)/len(trades)

    @staticmethod
    def full_report(eq, trades, ppy=252):
        tr = (eq[-1]-eq[0])/eq[0] if eq and eq[0]>0 else 0.0
        return {"total_return_pct":round(tr*100,2),"max_drawdown_pct":round(Metrics.max_drawdown(eq)*100,2),"sharpe_ratio":Metrics.sharpe_ratio(eq,ppy=ppy),"sortino_ratio":Metrics.sortino_ratio(eq,ppy=ppy),"total_trades":len(trades),"win_rate":Metrics.win_rate(trades),"profit_factor":Metrics.profit_factor(trades)}