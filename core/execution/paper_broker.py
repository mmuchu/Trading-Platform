import logging, time as _time
from dataclasses import dataclass
from typing import Optional
from config.settings import settings
logger = logging.getLogger(__name__)

@dataclass
class TradeRecord:
    timestamp: float; side: str; price: float; quantity: int; commission: float; slippage: float; equity_after: float

class PaperBroker:
    def __init__(self, initial_cash=None):
        cfg = settings.broker
        self.initial_cash = initial_cash or cfg.initial_cash
        self.cash = self.initial_cash; self.position = 0; self.entry_price = 0.0
        self.commission_pct = cfg.commission_pct; self.slippage_pct = cfg.slippage_pct
        self.trades = []; self.realised_pnl = 0.0; self.win_count = 0; self.loss_count = 0

    def execute(self, signal, price, timestamp=None):
        ts = timestamp or _time.time()
        if signal == "HOLD": return self.snapshot(price)
        exec_price = price * (1 + self.slippage_pct) if signal == "BUY" else price * (1 - self.slippage_pct)
        quantity = 1; gross = exec_price * quantity; comm = gross * self.commission_pct; total = gross + comm
        if signal == "BUY":
            if total > self.cash: return self.snapshot(price)
            self.cash -= total; self.position += quantity
            if self.position == 1: self.entry_price = exec_price
            elif self.position > 1: self.entry_price = (self.entry_price*(self.position-1)+exec_price)/self.position
        elif signal == "SELL":
            if self.position < 1: return self.snapshot(price)
            self.cash += gross - comm; self.position -= quantity
            pnl = (exec_price - self.entry_price)*quantity - comm
            self.realised_pnl += pnl
            if pnl > 0: self.win_count += 1
            else: self.loss_count += 1
            if self.position == 0: self.entry_price = 0.0
        eq = self.equity(price)
        self.trades.append(TradeRecord(timestamp=ts, side=signal, price=exec_price, quantity=quantity, commission=comm, slippage=abs(exec_price-price), equity_after=eq))
        return self.snapshot(price)

    def equity(self, cp): return self.cash + self.position * cp
    def unrealised_pnl(self, cp): return (cp - self.entry_price)*self.position if self.position else 0.0

    def snapshot(self, cp):
        eq = self.equity(cp); upnl = self.unrealised_pnl(cp)
        return {"cash":round(self.cash,2),"position":self.position,"entry_price":round(self.entry_price,2),"equity":round(eq,2),"unrealised_pnl":round(upnl,2),"realised_pnl":round(self.realised_pnl,2),"total_pnl":round(eq-self.initial_cash,2),"total_trades":len(self.trades),"win_count":self.win_count,"loss_count":self.loss_count,"win_rate":round(self.win_count/max(len(self.trades),1),4)}

    def get_trades(self):
        return [{"trade_id":i+1,"timestamp":t.timestamp,"side":t.side,"price":t.price,"quantity":t.quantity,"commission":t.commission,"slippage":t.slippage,"equity_after":t.equity_after} for i,t in enumerate(self.trades)]

    def reset(self):
        self.cash=self.initial_cash;self.position=0;self.entry_price=0.0;self.trades.clear();self.realised_pnl=0.0;self.win_count=0;self.loss_count=0