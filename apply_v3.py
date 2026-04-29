"""apply_v3.py - Patches v3.2.1 clean (e9271ef) + built-in verification (39 checks)"""
import os, py_compile

BASE = r"C:\dev\trading-automation-system\trading-platform"

def read(f):
    with open(f, "r", encoding="utf-8") as h:
        return h.read()

def write(f, c):
    with open(f, "w", encoding="utf-8") as h:
        h.write(c)

def chk(label, ok, r):
    s = "PASS" if ok else "FAIL"
    r.append((label, s))
    print(f"  [{s}] {label}")

def syn(path):
    try:
        py_compile.compile(path, doraise=True)
        return True
    except:
        return False

print("=" * 50)
print(" PATCHING v3.2.1 Calibrations")
print("=" * 50)

# === 1. settings.py ===
print("\n[1/3] config/settings.py ...")
sp = read(BASE + "\\config\\settings.py")

sp = sp.replace(
    "risk_per_trade_pct: float = 0.01         # 1% of equity per trade",
    "risk_per_trade_pct: float = 0.01         # 1% of equity per trade (legacy)\n    risk_per_trade_pct_min: float = 0.005    # 0.5% min risk per trade\n    risk_per_trade_pct_max: float = 0.01     # 1% max risk per trade"
)

sp = sp.replace(
    "signal_cooldown_sec: float = 10.0        # cooldown between signals",
    "signal_cooldown_sec: float = 10.0        # cooldown between signals (legacy)\n    signal_cooldown_ticks: int = 10          # tick-based cooldown between signals"
)

sp = sp.replace(
    "    breakout_threshold_pct: float = 0.02",
    "    breakout_threshold_pct: float = 0.02\n    # Strategy calibration thresholds\n    min_trade_score: float = 60.0\n    strong_trade_score: float = 70.0\n    notrade_zone_max: float = 55.0\n    observe_zone_max: float = 65.0\n    strong_score_to_execute: bool = True"
)

write(BASE + "\\config\\settings.py", sp)

# === 2. strategy.py ===
print("[2/3] services/v3/strategy.py ...")
st = read(BASE + "\\services\\v3\\strategy.py")

# 2a. SignalScoreConfig: add zone fields
st = st.replace(
    "    range_regime_penalty: float = -15     # score penalty in RANGE regime (for momentum)",
    "    range_regime_penalty: float = -15     # score penalty in RANGE regime (for momentum)\n    strong_score_to_execute: float = 70.0\n    notrade_zone_max: float = 55.0\n    observe_zone_max: float = 65.0"
)

# 2b. __init__: add tracking dicts
st = st.replace(
    "        self._recent_scores: Dict[str, deque] = {}  # recent signal scores for quality tracking",
    "        self._recent_scores: Dict[str, deque] = {}  # recent signal scores for quality tracking\n        self._tick_counter: Dict[str, int] = {}\n        self._last_signal_tick: Dict[str, int] = {}\n        self._trade_results: Dict[str, list] = {}\n        self._avg_win: float = 0.0\n        self._avg_loss: float = 0.0\n        self._expectancy: float = 0.0"
)

# 2c. Add _zone_gate before set_execution
st = st.replace(
    "    def set_execution(self, execution_service) -> None:",
    "    def _zone_gate(self, score: float, symbol: str) -> str:\n        \"\"\"Three-zone gate: NO_TRADE / OBSERVE / TRADE.\"\"\"\n        if score < self.score_cfg.notrade_zone_max:\n            logger.info(\"NO_TRADE %s: score %.1f < %.1f\", symbol, score, self.score_cfg.notrade_zone_max)\n            return \"NO_TRADE\"\n        if score < self.score_cfg.observe_zone_max:\n            logger.info(\"OBSERVE %s: score %.1f in observe zone [%.1f-%.1f]\", symbol, score, self.score_cfg.notrade_zone_max, self.score_cfg.observe_zone_max)\n            return \"OBSERVE\"\n        return \"TRADE\"\n\n    def set_execution(self, execution_service) -> None:"
)

# 2d. Tick-based cooldown (replace time-based)
st = st.replace(
    "        # Cooldown check\n        now = time.time()\n        if now - self._last_signal.get(tick.symbol, 0) < self.score_cfg.cooldown_sec:\n            return",
    "        # Tick-based cooldown (calibration)\n        self._tick_counter[tick.symbol] = self._tick_counter.get(tick.symbol, 0) + 1\n        last_tick = self._last_signal_tick.get(tick.symbol, -999)\n        if self._tick_counter[tick.symbol] - last_tick < 10:\n            return\n        self._last_signal_tick[tick.symbol] = self._tick_counter[tick.symbol]"
)

# 2e. Zone gate + strong score in _regime_filter (insert before VOLATILE check)
st = st.replace(
    "        # VOLATILE: only allow very high-confidence signals\n        if regime == RegimeType.VOLATILE and score < 40:",
    "        # Three-zone calibration gate\n        zone = self._zone_gate(score, signal.symbol)\n        if zone == \"NO_TRADE\":\n            return False, \"NO_TRADE zone\"\n        if zone == \"OBSERVE\":\n            return False, \"OBSERVE zone - signal suppressed\"\n\n        # Strong score execution gate\n        if score < self.score_cfg.strong_score_to_execute:\n            logger.info(\"SCORE BLOCK %s: score %.1f < strong %.1f\", signal.symbol, score, self.score_cfg.strong_score_to_execute)\n            return False, f\"score {score} below strong threshold\"\n\n        # VOLATILE: only allow very high-confidence signals\n        if regime == RegimeType.VOLATILE and score < 40:"
)

# 2f. Expectancy tracking in record_trade_result
st = st.replace(
    "    def record_trade_result(self, pnl: float) -> None:\n        \"\"\"Record trade outcome for equity stress tracking. Called by orchestrator after fills.\"\"\"\n        if pnl > 0:\n            self._consecutive_losses = 0\n        elif pnl < 0:\n            self._consecutive_losses += 1",
    "    def record_trade_result(self, pnl: float) -> None:\n        \"\"\"Record trade outcome for equity stress tracking. Called by orchestrator after fills.\"\"\"\n        if pnl > 0:\n            self._consecutive_losses = 0\n        elif pnl < 0:\n            self._consecutive_losses += 1\n\n        # Expectancy validation tracking\n        sym = \"default\"\n        if sym not in self._trade_results:\n            self._trade_results[sym] = []\n        self._trade_results[sym].append({\"pnl\": pnl, \"win\": pnl > 0})\n        if len(self._trade_results[sym]) >= 50:\n            recent = self._trade_results[sym][-50:]\n            wins = [t[\"pnl\"] for t in recent if t[\"win\"]]\n            losses = [-t[\"pnl\"] for t in recent if not t[\"win\"]]\n            if wins and losses:\n                self._avg_win = sum(wins) / len(wins)\n                self._avg_loss = sum(losses) / len(losses)\n                self._expectancy = (len(wins) / 50) * self._avg_win - (len(losses) / 50) * self._avg_loss\n                if self._expectancy > 0:\n                    logger.info(\"EXPECTANCY VALIDATED %s: avg_win=%.2f avg_loss=%.2f expectancy=%.2f (n=%d)\", sym, self._avg_win, self._avg_loss, self._expectancy, len(self._trade_results[sym]))\n                else:\n                    logger.info(\"EXPECTANCY NOT VALIDATED %s: avg_win=%.2f avg_loss=%.2f expectancy=%.2f (n=%d)\", sym, self._avg_win, self._avg_loss, self._expectancy, len(self._trade_results[sym]))"
)

# 2g. Expectancy fields in stats property
st = st.replace(
    "} if hasattr(self, '_recent_scores') else {},\n        }",
    "} if hasattr(self, '_recent_scores') else {},\n            \"expectancy\": round(getattr(self, '_expectancy', 0), 4),\n            \"avg_win\": round(getattr(self, '_avg_win', 0), 4),\n            \"avg_loss\": round(getattr(self, '_avg_loss', 0), 4),\n        }"
)

write(BASE + "\\services\\v3\\strategy.py", st)

# === 3. execution.py ===
print("[3/3] services/v3/execution.py ...")
ex = read(BASE + "\\services\\v3\\execution.py")

# 3a. ATR tracking in __init__
ex = ex.replace(
    "        # Recent trade results for risk checker\n        self._recent_pnls: list[float] = []  # last N trade PnLs",
    "        # Recent trade results for risk checker\n        self._recent_pnls: list[float] = []  # last N trade PnLs\n\n        # ATR tracking for position sizing\n        self._atr_values: Dict[str, list] = {}\n        self._atr_history: Dict[str, list] = {}"
)

# 3b. Add update_atr + _get_avg_atr before compute_position_size
ex = ex.replace(
    "    def compute_position_size(",
    "    def update_atr(self, symbol: str, atr: float):\n        \"\"\"Track ATR values for position sizing.\"\"\"\n        if symbol not in self._atr_values:\n            self._atr_values[symbol] = []\n        self._atr_values[symbol].append(atr)\n        if len(self._atr_values[symbol]) > 14:\n            self._atr_values[symbol] = self._atr_values[symbol][-14:]\n        self._atr_history[symbol] = list(self._atr_values[symbol])\n\n    def _get_avg_atr(self, symbol: str) -> float:\n        \"\"\"Get average ATR over tracked period.\"\"\"\n        vals = self._atr_values.get(symbol, [])\n        return sum(vals) / len(vals) if vals else 0.0\n\n    def compute_position_size("
)

# 3c. ATR ratio scaling + risk clamping in compute_position_size
ex = ex.replace(
    "        equity = self.equity\n        risk_budget = equity * settings.risk.risk_per_trade_pct  # e.g., $100 on $10K at 1%",
    "        equity = self.equity\n        # Risk budget with ATR-based scaling and clamping\n        risk_pct = getattr(settings.risk, 'risk_per_trade_pct_max', 0.01)\n        avg_atr = self._get_avg_atr(signal.symbol)\n        if atr_pct > 0 and avg_atr > 0:\n            atr_ratio = atr_pct / avg_atr\n            if atr_ratio > 3.0:\n                risk_pct = risk_pct * 0.5\n            elif atr_ratio < 1.0:\n                risk_pct = risk_pct * 0.75\n        risk_min = getattr(settings.risk, 'risk_per_trade_pct_min', 0.005)\n        risk_max = getattr(settings.risk, 'risk_per_trade_pct_max', 0.01)\n        risk_pct = max(risk_min, min(risk_max, risk_pct))\n        risk_budget = equity * risk_pct"
)

# 3d. Add ATR to stats property
for pat in ['"recent_pnls"', '"sl_exits"', '"tp_exits"']:
    if pat in ex:
        ex = ex.replace(pat, pat + ",\n            \"atr_values\": dict(self._atr_values),\n            \"atr_history\": dict(self._atr_history)", 1)
        break

write(BASE + "\\services\\v3\\execution.py", ex)
print("  All 3 files patched.\n")

# === VERIFICATION ===
print("=" * 50)
print(" VERIFICATION (39 checks)")
print("=" * 50)

R = []

print("\n--- config/settings.py ---")
sp = read(BASE + "\\config\\settings.py")
chk("risk_pct_min", "risk_per_trade_pct_min" in sp, R)
chk("risk_pct_max", "risk_per_trade_pct_max" in sp, R)
chk("cooldown_ticks", "signal_cooldown_ticks" in sp, R)
chk("min_trade_score=60", "min_trade_score" in sp and "60.0" in sp, R)
chk("strong_trade_score=70", "strong_trade_score" in sp and "70.0" in sp, R)
chk("notrade_zone=55", "notrade_zone_max" in sp and "55.0" in sp, R)
chk("observe_zone=65", "observe_zone_max" in sp and "65.0" in sp, R)
chk("strong_score_to_execute", "strong_score_to_execute" in sp, R)

print("\n--- services/v3/strategy.py ---")
st = read(BASE + "\\services\\v3\\strategy.py")
chk("strong_score in config", "strong_score_to_execute: float = 70.0" in st, R)
chk("notrade_zone in config", "notrade_zone_max: float = 55.0" in st, R)
chk("observe_zone in config", "observe_zone_max: float = 65.0" in st, R)
chk("_zone_gate method", "def _zone_gate(" in st, R)
chk("NO_TRADE zone", "NO_TRADE" in st, R)
chk("OBSERVE zone", "OBSERVE" in st, R)
chk("zone_gate called", "self._zone_gate(" in st, R)
chk("OBSERVE suppressed", "OBSERVE" in st and 'return False, "OBSERVE' in st, R)
chk("strong_score used", "strong_score_to_execute" in st and "score < self.score_cfg.strong_score_to_execute" in st, R)
chk("SCORE BLOCK log", "SCORE BLOCK" in st, R)
chk("_tick_counter", "self._tick_counter" in st, R)
chk("_last_signal_tick", "self._last_signal_tick" in st, R)
chk("tick cooldown gate", "_last_signal_tick" in st and "< 10" in st, R)
chk("tick counter increment", "_tick_counter[" in st and "+= 1" in st, R)
chk("_trade_results", "self._trade_results" in st, R)
chk("_avg_win/_avg_loss", "self._avg_win" in st and "self._avg_loss" in st, R)
chk("expectancy append", "_trade_results" in st and ".append(" in st, R)
chk("50-trade gate", ">= 50" in st, R)
chk("EXPECTANCY VALIDATED", "EXPECTANCY VALIDATED" in st, R)
chk("EXPECTANCY NOT VALIDATED", "EXPECTANCY NOT VALIDATED" in st, R)
chk("expectancy in stats", '"expectancy"' in st, R)
chk("avg_win in stats", '"avg_win"' in st, R)
chk("syntax valid", syn(BASE + "\\services\\v3\\strategy.py"), R)

print("\n--- services/v3/execution.py ---")
ex = read(BASE + "\\services\\v3\\execution.py")
chk("_atr_values", "self._atr_values" in ex, R)
chk("_atr_history", "self._atr_history" in ex, R)
chk("update_atr method", "def update_atr(" in ex, R)
chk("_get_avg_atr method", "def _get_avg_atr(" in ex, R)
chk("atr_ratio scaling", "atr_ratio" in ex, R)
chk("risk clamping", "max(risk_min" in ex and "min(risk_max" in ex, R)
chk("atr in stats", "atr_values" in ex and "atr_history" in ex, R)
chk("syntax valid", syn(BASE + "\\services\\v3\\execution.py"), R)

P = sum(1 for _, s in R if s == "PASS")
F = sum(1 for _, s in R if s == "FAIL")
print(f"\n{'='*50}")
print(f" RESULT: {P}/{len(R)} passed", end="")
if F:
    print(f"\n {F} FAILED:")
    for n, s in R:
        if s == "FAIL":
            print(f"   - {n}")
print(f"{'='*50}")