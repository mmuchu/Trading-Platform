import logging
import math
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class MarketState(Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"
    UNKNOWN = "unknown"


@dataclass
class OHLCV:
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def body(self) -> float:
        return abs(self.close - self.open)

    @property
    def upper_shadow(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_shadow(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open


@dataclass
class MarketAnalysis:
    symbol: str
    market_state: MarketState
    trend_strength: float
    volatility_pct: float
    rsi: float
    volume_ratio: float
    support_levels: List[float]
    resistance_levels: List[float]
    atr: float
    moving_averages: Dict[str, float]
    timestamp: str


class MarketAnalyzer:

    def __init__(self):
        self._price_cache: Dict[str, List[OHLCV]] = {}
        self._latest_prices: Dict[str, float] = {}
        logger.info("MarketAnalyzer initialized")

    def update_ohlcv(self, symbol: str, candles: List[Dict]):
        ohlcv_list = []
        for c in candles:
            ohlcv_list.append(OHLCV(
                timestamp=c.get("timestamp", ""),
                open=c.get("open", 0), high=c.get("high", 0),
                low=c.get("low", 0), close=c.get("close", 0),
                volume=c.get("volume", 0),
            ))
        self._price_cache[symbol] = ohlcv_list
        if ohlcv_list:
            self._latest_prices[symbol] = ohlcv_list[-1].close
        logger.debug(f"Updated OHLCV for {symbol}: {len(ohlcv_list)} candles")

    def analyze(self, symbol: str) -> Optional[MarketAnalysis]:
        candles = self._price_cache.get(symbol, [])
        if len(candles) < 20:
            logger.warning(f"Insufficient data for {symbol}: {len(candles)} candles (need 20+)")
            return None
        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        volumes = [c.volume for c in candles]
        sma_20 = self._sma(closes, 20)
        sma_50 = self._sma(closes, 50) if len(closes) >= 50 else self._sma(closes, len(closes))
        ema_12 = self._ema(closes, 12)
        ema_26 = self._ema(closes, 26)
        rsi = self._rsi(closes, 14)
        atr = self._atr(candles, 14)
        avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else sum(volumes) / len(volumes)
        current_vol = volumes[-1] if volumes else 0
        vol_ratio = current_vol / avg_vol if avg_vol > 0 else 1.0
        state = self._determine_state(closes, sma_20, atr, rsi)
        trend_strength = self._trend_strength(closes, sma_20, sma_50)
        volatility_pct = self._volatility_pct(closes)
        supports, resistances = self._find_sr_levels(candles)
        mas = {"sma_20": round(sma_20, 4), "sma_50": round(sma_50, 4),
               "ema_12": round(ema_12, 4), "ema_26": round(ema_26, 4)}
        return MarketAnalysis(
            symbol=symbol, market_state=state,
            trend_strength=round(trend_strength, 4),
            volatility_pct=round(volatility_pct, 4),
            rsi=round(rsi, 4), volume_ratio=round(vol_ratio, 4),
            support_levels=supports, resistance_levels=resistances,
            atr=round(atr, 6), moving_averages=mas,
            timestamp=datetime.now().isoformat(),
        )

    def _sma(self, data: List[float], period: int) -> float:
        if len(data) < period:
            return sum(data) / len(data)
        return sum(data[-period:]) / period

    def _ema(self, data: List[float], period: int) -> float:
        if len(data) < period:
            return sum(data) / len(data)
        multiplier = 2 / (period + 1)
        ema = sum(data[:period]) / period
        for price in data[period:]:
            ema = (price - ema) * multiplier + ema
        return ema

    def _rsi(self, closes: List[float], period: int = 14) -> float:
        if len(closes) < period + 1:
            return 50.0
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        for i in range(period, len(deltas)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _atr(self, candles: List[OHLCV], period: int = 14) -> float:
        if len(candles) < 2:
            return 0.0
        trs = []
        for i in range(1, len(candles)):
            tr = max(candles[i].high - candles[i].low,
                     abs(candles[i].high - candles[i - 1].close),
                     abs(candles[i].low - candles[i - 1].close))
            trs.append(tr)
        if len(trs) < period:
            return sum(trs) / len(trs)
        atr = sum(trs[:period]) / period
        for i in range(period, len(trs)):
            atr = (atr * (period - 1) + trs[i]) / period
        return atr

    def _determine_state(self, closes: List[float], sma_20: float, atr: float, rsi: float) -> MarketState:
        if len(closes) < 20:
            return MarketState.UNKNOWN
        recent = closes[-5:]
        recent_avg = sum(recent) / len(recent)
        price_change = (closes[-1] - closes[-20]) / closes[-20] * 100 if closes[-20] > 0 else 0
        volatility = self._volatility_pct(closes[-20:])
        if volatility > 5.0:
            return MarketState.HIGH_VOLATILITY
        if price_change > 2.0 and recent_avg > sma_20:
            return MarketState.TRENDING_UP
        elif price_change < -2.0 and recent_avg < sma_20:
            return MarketState.TRENDING_DOWN
        elif abs(price_change) < 1.0:
            return MarketState.RANGING
        elif recent_avg > sma_20:
            return MarketState.TRENDING_UP
        else:
            return MarketState.TRENDING_DOWN

    def _trend_strength(self, closes: List[float], sma_20: float, sma_50: float) -> float:
        if len(closes) < 20 or sma_20 == 0:
            return 0.0
        price_vs_sma = (closes[-1] - sma_20) / sma_20 * 100
        spread = (sma_20 - sma_50) / sma_50 * 100 if sma_50 > 0 else 0
        strength = (price_vs_sma + spread) / 2
        return max(-1.0, min(1.0, strength / 5.0))

    def _volatility_pct(self, data: List[float], period: int = 20) -> float:
        if len(data) < 2:
            return 0.0
        window = data[-period:]
        returns = [(window[i] - window[i - 1]) / window[i - 1] for i in range(1, len(window))]
        if not returns:
            return 0.0
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / len(returns)
        return math.sqrt(variance) * 100 * math.sqrt(252)

    def _find_sr_levels(self, candles: List[OHLCV], num_levels: int = 3) -> Tuple[List[float], List[float]]:
        if len(candles) < 10:
            return [], []
        recent = candles[-50:] if len(candles) > 50 else candles
        lows = sorted([c.low for c in recent])
        highs = sorted([c.high for c in recent], reverse=True)
        supports = []
        for i in range(0, len(lows) - 1):
            if i >= num_levels:
                break
            cluster = [lows[i]]
            for j in range(i + 1, min(i + 5, len(lows))):
                if abs(lows[j] - lows[i]) / lows[i] < 0.005:
                    cluster.append(lows[j])
            supports.append(round(sum(cluster) / len(cluster), 2))
        supports = list(set(supports))[:num_levels]
        resistances = []
        for i in range(0, len(highs) - 1):
            if i >= num_levels:
                break
            cluster = [highs[i]]
            for j in range(i + 1, min(i + 5, len(highs))):
                if abs(highs[j] - highs[i]) / highs[i] < 0.005:
                    cluster.append(highs[j])
            resistances.append(round(sum(cluster) / len(cluster), 2))
        resistances = list(set(resistances))[:num_levels]
        return sorted(supports), sorted(resistances)

    def get_latest_price(self, symbol: str) -> Optional[float]:
        return self._latest_prices.get(symbol)

    def get_status(self) -> Dict:
        return {
            "symbols_tracked": len(self._price_cache),
            "symbols": list(self._price_cache.keys()),
            "latest_prices": dict(self._latest_prices),
        }
