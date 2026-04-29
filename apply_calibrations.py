"""
apply_calibrations.py — Strategy calibration patcher v2
Patches v3.2.1 base (commit e9271ef) with all 5 calibration changes.
"""

import re

BASE = "C:\\dev\\trading-automation-system\\trading-platform"

def read(f):
    with open(f, "r", encoding="utf-8") as fh:
        return fh.read()

def write(f, c):
    with open(f, "w", encoding="utf-8") as fh:
        fh.write(c)

# ── 1. settings.py ──────────────────────────────────────────────
print("Patching settings.py ...")
sp = read(f"{BASE}\\config\\settings.py")

# A: risk_per_trade_pct → risk_per_trade_pct_min / risk_per_trade_pct_max
sp = sp.replace(
    "risk_per_trade_pct: float = 1.0",
    "risk_per_trade_pct_min: float = 0.5\n    risk_per_trade_pct_max: float = 1.0"
)

# B: signal_cooldown_sec → signal_cooldown_ticks + default
sp = sp.replace(
    "signal_cooldown_sec: float = 30.0",
    "# Calibration: tick-based cooldown (replaces time-based)\n    signal_cooldown_ticks: int = 10"
)

# C: Add calibration zone thresholds after signal_cooldown_ticks line
if "notrade_zone_max" not in sp:
    sp = sp.replace(
        "signal_cooldown_ticks: int = 10",
        "signal_cooldown_ticks: int = 10\n    notrade_zone_max: float = 55.0\n    observe_zone_max: float = 65.0"
    )

# D: min_trade_score = 60
sp = sp.replace(
    "min_trade_score: float = 45.0",
    "min_trade_score: float = 60.0"
)

# E: strong_trade_score = 70
if "strong_trade_score" not in sp:
    sp = sp.replace(
        "min_trade_score: float = 60.0",
        "min_trade_score: float = 60.0\n    strong_trade_score: float = 70.0"
    )

# F: strong_score_to_execute = True
if "strong_score_to_execute" not in sp:
    sp = sp.replace(
        "strong_trade_score: float = 70.0",
        "strong_trade_score: float = 70.0\n    strong_score_to_execute: bool = True"
    )

write(f"{BASE}\\config\\settings.py", sp)
print("  settings.py OK")

# ── 2. strategy.py ──────────────────────────────────────────────
print("Patching strategy.py ...")
st = read(f"{BASE}\\services\\v3\\strategy.py")

# ---- 2a. SignalScoreConfig: add new fields ----
old_config = """\
class SignalScoreConfig:
    min_score_to_emit: float = 45.0"""

new_config = """\
class SignalScoreConfig:
    min_score_to_emit: float = 60.0
    strong_score_to_execute: float = 70.0
    notrade_zone_max: float = 55.0
    observe_zone_max: float = 65.0"""

st = st.replace(old_config, new_config)

# ---- 2b. Add _zone_gate method after __init__ ----
# Find the end of __init__ and insert _zone_gate after it
if "_zone_gate" not in st:
    # Look for the pattern where __init__ ends and the next method begins
    # Insert _zone_gate method before the first non-__init__ method
    gate_method = """
    def _zone_gate(self, score: float, symbol: str) -> str:
        \"\"\"Three-zone gate: NO_TRADE / OBSERVE / TRADE.\"\"\"
        if score < self.score_cfg.notrade_zone_max:
            self._log("NO_TRADE", symbol, f"score {score:.1f} < {self.score_cfg.notrade_zone_max}")
            return "NO_TRADE"
        if score < self.score_cfg.observe_zone_max:
            self._log("OBSERVE", symbol, f"score {score:.1f} in observe zone [{self.score_cfg.notrade_zone_max}-{self.score_cfg.observe_zone_max}]")
            return "OBSERVE"
        return "TRADE"
"""
    # Insert before the first def that is not __init__
    # Find end of __init__ by looking for next method definition
    lines = st.split("\n")
    insert_idx = None
    in_init = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("def __init__"):
            in_init = True
        elif in_init and (stripped.startswith("def ") and not stripped.startswith("def __")):
            insert_idx = i
            break
        elif in_init and stripped.startswith("class "):
            insert_idx = i
            break

    if insert_idx is not None:
        lines.insert(insert_idx, gate_method)
        st = "\n".join(lines)

# ---- 2c. Replace old regime filter block with zone-gated version ----
old_regime = """\
            if regime == "VOLATILE":
                if score < 40:
                    return None
            elif regime == "RANGE":
                if score < min_score_to_emit + 0:
                    return None"""

new_regime = """\
            # Three-zone calibration gate
            zone = self._zone_gate(score, symbol)
            if zone == "NO_TRADE":
                return None
            if zone == "OBSERVE":
                return None
            if hasattr(self.score_cfg, 'strong_score_to_execute') and self.score_cfg.strong_score_to_execute:
                if score < self.score_cfg.strong_score_to_execute:
                    return None"""

st = st.replace(old_regime, new_regime)

# ---- 2d. Add SCORE BLOCK log after strong_score gate ----
if "SCORE BLOCK" not in st:
    # The strong_score check block above ends with "return None"
    # Add a log line just before it
    st = st.replace(
        "if hasattr(self.score_cfg, 'strong_score_to_execute') and self.score_cfg.strong_score_to_execute:\n                if score < self.score_cfg.strong_score_to_execute:\n                    return None",
        "if hasattr(self.score_cfg, 'strong_score_to_execute') and self.score_cfg.strong_score_to_execute:\n                if score < self.score_cfg.strong_score_to_execute:\n                    self._log('SCORE BLOCK', symbol, f'score {score:.1f} < strong {self.score_cfg.strong_score_to_execute}')\n                    return None"
    )

# ---- 2e. Add expectancy tracking to record_trade_result ----
old_record = """\
    def record_trade_result(self, symbol: str, pnl: float, win: bool):
        if symbol not in self.trade_history:
            self.trade_history[symbol] = []
        self.trade_history[symbol].append({"pnl": pnl, "win": win, "ts": time.time()})"""

new_record = """\
    def record_trade_result(self, symbol: str, pnl: float, win: bool):
        if symbol not in self.trade_history:
            self.trade_history[symbol] = []
        self.trade_history[symbol].append({"pnl": pnl, "win": win, "ts": time.time()})
        # Expectancy validation tracking
        hist = self.trade_history[symbol]
        if len(hist) >= 50:
            wins = [t["pnl"] for t in hist[-50:] if t["win"]]
            losses = [-t["pnl"] for t in hist[-50:] if not t["win"]]
            if wins and losses:
                avg_w = sum(wins) / len(wins)
                avg_l = sum(losses) / len(losses)
                exp = (len(wins) / 50) * avg_w - (len(losses) / 50) * avg_l
                if exp > 0:
                    self._log("EXPECTANCY VALIDATED", symbol,
                              f"avg_win={avg_w:.2f} avg_loss={avg_l:.2f} expectancy={exp:.2f} (n={len(hist)})")
                else:
                    self._log("EXPECTANCY NOT VALIDATED", symbol,
                              f"avg_win={avg_w:.2f} avg_loss={avg_l:.2f} expectancy={exp:.2f} (n={len(hist)})")"""

st = st.replace(old_record, new_record)

# ---- 2f. Tick cooldown tracking ----
if "_last_signal_tick" not in st:
    # Add tick tracking dict in __init__
    st = st.replace(
        "self.trade_history: dict = {}",
        "self.trade_history: dict = {}\n        self._last_signal_tick: dict = {}"
    )

if "signal_cooldown_ticks" not in st:
    # Add cooldown check at the start of generate_signal
    # Find the generate_signal method and add tick cooldown after the regime check
    cooldown_block = """
        # Tick-based signal cooldown
        if hasattr(self.settings, 'signal_cooldown_ticks'):
            cooldown = self.settings.signal_cooldown_ticks
        else:
            cooldown = 10
        last_tick = self._last_signal_tick.get(symbol, -999)
        if current_tick - last_tick < cooldown:
            return None
        self._last_signal_tick[symbol] = current_tick"""

    # Insert after regime check, before the main signal logic
    if "regime ==" in st and cooldown_block.strip() not in st:
        st = st.replace(
            "            regime = self.regime_detector.get_regime(symbol)",
            "            regime = self.regime_detector.get_regime(symbol)" + cooldown_block
        )

write(f"{BASE}\\services\\v3\\strategy.py", st)
print("  strategy.py OK")

# ── 3. execution.py ─────────────────────────────────────────────
print("Patching execution.py ...")
ex = read(f"{BASE}\\services\\v3\\execution.py")

# ---- 3a. Add ATR tracking dict in __init__ ----
if "_atr_values" not in ex:
    ex = ex.replace(
        "self.positions: dict = {}",
        "self.positions: dict = {}\n        self._atr_values: dict = {}"
    )

# ---- 3b. Add update_atr and _get_avg_atr methods ----
if "_get_avg_atr" not in ex:
    atr_methods = """
    def update_atr(self, symbol: str, atr: float):
        \"\"\"Track ATR values for position sizing.\"\"\"
        if symbol not in self._atr_values:
            self._atr_values[symbol] = []
        self._atr_values[symbol].append(atr)
        if len(self._atr_values[symbol]) > 14:
            self._atr_values[symbol] = self._atr_values[symbol][-14:]

    def _get_avg_atr(self, symbol: str) -> float:
        \"\"\"Get average ATR over tracked period.\"\"\"
        vals = self._atr_values.get(symbol, [])
        return sum(vals) / len(vals) if vals else 0.0
"""
    # Insert before compute_position_size
    lines = ex.split("\n")
    insert_idx = None
    for i, line in enumerate(lines):
        if "def compute_position_size" in line:
            insert_idx = i
            break
    if insert_idx is not None:
        lines.insert(insert_idx, atr_methods)
        ex = "\n".join(lines)

# ---- 3c. Replace compute_position_size with ATR-integrated version ----
old_compute = """\
    def compute_position_size(self, symbol: str, entry_price: float,
                              stop_loss: float, risk_pct: float = 1.0) -> float:
        if stop_loss <= 0 or entry_price <= 0:
            return 0.0
        risk_per_unit = abs(entry_price - stop_loss)
        if risk_per_unit <= 0:
            return 0.0
        position_size = (balance * risk_pct / 100) / risk_per_unit
        return round(position_size, 6)"""

new_compute = """\
    def compute_position_size(self, symbol: str, entry_price: float,
                              stop_loss: float, risk_pct: float = 1.0) -> float:
        if stop_loss <= 0 or entry_price <= 0:
            return 0.0
        risk_per_unit = abs(entry_price - stop_loss)
        if risk_per_unit <= 0:
            return 0.0
        # ATR-based risk scaling
        avg_atr = self._get_avg_atr(symbol)
        if avg_atr > 0:
            atr_ratio = risk_per_unit / avg_atr
            if atr_ratio > 3.0:
                risk_pct = risk_pct * 0.5  # Wide stops: halve risk
            elif atr_ratio < 1.0:
                risk_pct = risk_pct * 0.75  # Tight stops: reduce risk
        # Risk clamping to 0.5%-1.0%
        if hasattr(self.settings, 'risk_per_trade_pct_min'):
            risk_pct = max(self.settings.risk_per_trade_pct_min, risk_pct)
        if hasattr(self.settings, 'risk_per_trade_pct_max'):
            risk_pct = min(self.settings.risk_per_trade_pct_max, risk_pct)
        position_size = (balance * risk_pct / 100) / risk_per_unit
        return round(position_size, 6)"""

ex = ex.replace(old_compute, new_compute)

write(f"{BASE}\\services\\v3\\execution.py", ex)
print("  execution.py OK")

print("\\nDone. Run verify_calibrations.py to check.")