"""Correlation Matrix - rolling correlation between asset returns."""
import logging
import threading
from collections import defaultdict
from typing import Dict, List, Optional, Tuple
import numpy as np

logger = logging.getLogger(__name__)
DEFAULT_CORRELATION = 0.0
MIN_OBSERVATIONS = 30

class CorrelationMatrix:
    def __init__(self, window: int = 100):
        self.window = window
        self._returns: Dict[str, List[float]] = defaultdict(list)
        self._last_prices: Dict[str, float] = {}
        self._lock = threading.RLock()
        self._cached_matrix: Optional[Dict[str, Dict[str, float]]] = None

    def update(self, prices: Dict[str, float]) -> None:
        with self._lock:
            for symbol, price in prices.items():
                if symbol in self._last_prices and self._last_prices[symbol] > 0:
                    prev = self._last_prices[symbol]
                    ret = (price - prev) / prev
                    self._returns[symbol].append(ret)
                    if len(self._returns[symbol]) > self.window:
                        self._returns[symbol] = self._returns[symbol][-self.window:]
                self._last_prices[symbol] = price
            self._cached_matrix = None

    def get_correlation(self, sym_a: str, sym_b: str) -> float:
        with self._lock:
            if sym_a == sym_b:
                return 1.0
            rets_a = self._returns.get(sym_a, [])
            rets_b = self._returns.get(sym_b, [])
            if len(rets_a) < MIN_OBSERVATIONS or len(rets_b) < MIN_OBSERVATIONS:
                return DEFAULT_CORRELATION
            min_len = min(len(rets_a), len(rets_b))
            a = np.array(rets_a[-min_len:])
            b = np.array(rets_b[-min_len:])
            if np.std(a) < 1e-10 or np.std(b) < 1e-10:
                return DEFAULT_CORRELATION
            corr = np.corrcoef(a, b)[0, 1]
            return float(np.clip(corr, -1.0, 1.0)) if not np.isnan(corr) else DEFAULT_CORRELATION

    def get_matrix(self) -> Dict[str, Dict[str, float]]:
        with self._lock:
            if self._cached_matrix is not None:
                return self._cached_matrix
            symbols = list(self._returns.keys())
            matrix: Dict[str, Dict[str, float]] = {}
            for sym_a in symbols:
                matrix[sym_a] = {}
                for sym_b in symbols:
                    matrix[sym_a][sym_b] = self.get_correlation(sym_a, sym_b)
            self._cached_matrix = matrix
            return matrix

    def effective_exposure(self, positions: Dict[str, float]) -> float:
        symbols = [s for s, v in positions.items() if abs(v) > 0]
        if not symbols:
            return 0.0
        if len(symbols) == 1:
            return abs(list(positions.values())[0])
        w = np.array([positions.get(s, 0.0) for s in symbols])
        corr_matrix = np.eye(len(symbols))
        full_matrix = self.get_matrix()
        for i, sym_a in enumerate(symbols):
            for j, sym_b in enumerate(symbols):
                if i != j:
                    corr_matrix[i][j] = full_matrix.get(sym_a, {}).get(sym_b, DEFAULT_CORRELATION)
        effective = float(np.sqrt(max(np.dot(w, np.dot(corr_matrix, w)), 0.0)))
        return effective

    def highest_correlated_pairs(self, threshold: float = 0.7) -> List[Tuple[str, str, float]]:
        pairs = []
        matrix = self.get_matrix()
        seen = set()
        for sym_a, row in matrix.items():
            for sym_b, corr in row.items():
                if sym_a != sym_b and (sym_b, sym_a) not in seen:
                    seen.add((sym_a, sym_b))
                    if abs(corr) >= threshold:
                        pairs.append((sym_a, sym_b, corr))
        pairs.sort(key=lambda x: abs(x[2]), reverse=True)
        return pairs

    def reset(self) -> None:
        with self._lock:
            self._returns.clear()
            self._returns.clear()
            self._returns.clear()
            self._last_prices.clear()
            self._cached_matrix = None
