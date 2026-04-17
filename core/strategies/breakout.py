from typing import Optional
from config.settings import settings

class BreakoutStrategy:
    name = "breakout"
    def __init__(self, window=None, threshold_pct=None):
        self.window = window or settings.strategy.breakout_window
        self.threshold_pct = threshold_pct or settings.strategy.breakout_threshold_pct

    def generate(self, price, history):
        if len(history) < self.window: return "HOLD"
        recent = history[-self.window:]
        high, low = max(recent), min(recent)
        range_size = high - low
        if range_size == 0: return "HOLD"
        bl = range_size * self.threshold_pct
        if price > high + bl: return "BUY"
        elif price < low - bl: return "SELL"
        return "HOLD"