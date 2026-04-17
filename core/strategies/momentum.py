from typing import Optional
from config.settings import settings

class MomentumStrategy:
    name = "momentum"
    def __init__(self, lookback=None):
        self.lookback = lookback or settings.strategy.momentum_lookback

    def generate(self, price, history):
        if len(history) < self.lookback: return "HOLD"
        sma = sum(history[-self.lookback:]) / self.lookback
        if price > sma: return "BUY"
        elif price < sma: return "SELL"
        return "HOLD"