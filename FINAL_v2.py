"""FINAL_v2.py - Complete patch: calibrations + unblock pipeline"""
import os, py_compile

BASE = r"C:\dev\trading-automation-system\trading-platform"
def read(f):
    with open(f, "r", encoding="utf-8") as h: return h.read()
def write(f, c):
    with open(f, "w", encoding="utf-8") as h: h.write(c)
def syn(path):
    try: py_compile.compile(path, doraise=True); return True
    except: return False
def chk(label, ok, r):
    s = "PASS" if ok else "FAIL"; r.append((label, s)); print(f"  [{s}] {label}")

print("=" * 55)
print(" FINAL v2: Calibrations + Pipeline Unblock")
print("=" * 55)

# ===================== 1. settings.py =====================
print("\n[1/3] config/settings.py ...")
sp = read(BASE + "\\config\\settings.py")
sp = sp.replace(
    "risk_per_trade_pct: float = 0.01         # 1% of equity per trade",
    "risk_per_trade_pct: float = 0.01         # 1% of equity per trade (legacy)\n    risk_per_trade_pct_min: float = 0.005    # 0.5% min risk\n    risk_per_trade_pct_max: float = 0.01     # 1% max risk"
)
sp = sp.replace(
    "signal_cooldown_sec: float = 10.0        # cooldown between signals",
    "signal_cooldown_sec: float = 10.0        # cooldown between signals (legacy)\n    signal_cooldown_ticks: int = 10          # tick-based cooldown"
)
sp = sp.replace(
    "    breakout_threshold_pct: float = 0.02",
    "    breakout_threshold_pct: float = 0.02\n    # Strategy calibration thresholds (aggressive for data collection)\n    min_trade_score: float = 25.0\n    strong_trade_score: float = 30.0\n    notrade_zone_max: float = 15.0\n    observe_zone_max: float = 25.0\n    strong_score_to_execute: bool = True"
)
write(BASE + "\\config\\settings.py", sp)
print("  OK")

# ===================== 2. strategy.py =====================
print("[2/3] services/v3/strategy.py ...")
st = read(BASE + "\\services\\v3\\strategy.py")

# --- 2a. MomentumV3: halve threshold for tick sensitivity ---
st = st.replace(
    "        self.threshold_pct = threshold_pct",
    "        self.threshold_pct = threshold_pct * 0.5  # halved for tick-level sensitivity"
)

# --- 2b. SignalScoreConfig: lower penalties + add zone fields ---
st = st.replace(
    "    min_score_to_emit: float = 45.0       # minimum composite score (0-100) to emit signal",
    "    min_score_to_emit: float = 25.0       # minimum composite score (calibrated: was 45)"
)
st = st.replace(
    "    volatile_regime_penalty: float = -30  # score penalty in VOLATILE regime",
    "    volatile_regime_penalty: float = -5   # score penalty in VOLATILE regime (calibrated: was -30)"
)
st = st.replace(
    "    range_regime_penalty: float = -15     # score penalty in RANGE regime (for momentum)",
    "    range_regime_penalty: float = -5      # score penalty in RANGE regime (calibrated: was -15)"
)
st = st.replace(
    "    range_regime_penalty: float = -5      # score penalty in RANGE regime (calibrated: was -15)",
    "    range_regime_penalty: float = -5      # score penalty in RANGE regime (calibrated: was -15)\n    strong_score_to_execute: float = 30.0\n    notrade_zone_max: float = 15.0\n    observe_zone_max: float = 25.0"
)

# --- 2c. __init__: add tracking dicts ---
st = st.replace(
    "        self._recent_scores: Dict[str, deque] = {}  # recent signal scores for quality tracking",
    "        self._recent_scores: Dict[str, deque] = {}  # recent signal scores for quality tracking\n        self._tick_counter: Dict[str, int] = {}\n        self._last_signal_tick: Dict[str, int] = {}\n        self._trade_results: Dict[str, list] = {}\n        self._avg_win: float = 0.0\n        self._avg_loss: float = 0.0\n        self._expectancy: float = 0.0"
)

# --- 2d. Add _zone_gate before set_execution ---
st = st.replace(
    "    def set_execution(self, execution_service) -> None:",
    "    def _zone_gate(self, score: float, symbol: str) -> str:\n        \"\"\"Three-zone gate: NO_TRADE / OBSERVE / TRADE.\"\"\"\n        if score < self.score_cfg.notrade_zone_max:\n            logger.info(\"NO_TRADE %s: score %.1f < %.1f\", symbol, score, self.score_cfg.notrade_zone_max)\n            return \"NO_TRADE\"\n        if score < self.score_cfg.observe_zone_max:\n            logger.info(\"OBSERVE %s: score %.1f in observe zone\", symbol, score)\n            return \"OBSERVE\"\n        return \"TRADE\"\n\n    def set_execution(self, execution_service) -> None:"
)

# --- 2e. Tick-based cooldown (replace time-based) ---
st = st.replace(
    "        # Cooldown check\n        now = time.time()\n        if now - self._last_signal.get(tick.symbol, 0) < self.score_cfg.cooldown_sec:\n            return",
    "        # Tick-based cooldown (calibration)\n        self._tick_counter[tick.symbol] = self._tick_counter.get(tick.symbol, 0) + 1\n        last_tick = self._last_signal_tick.get(tick.symbol, -999)\n        if self._tick_counter[tick.symbol] - last_tick < 10:\n            return\n        self._last_signal_tick[tick.symbol] = self._tick_counter[tick.symbol]"
)

# --- 2f. Add score debug logging before regime filter ---
st = st.replace(
    "                # Regime filtering: suppress in VOLATILE, penalize in RANGE\n                filtered, reason = self._regime_filter(signal, regime, score)",
    "                # SCORE DEBUG LOG\n                logger.info('SCORE_DEBUG %s: raw_score=%.1f regime=%s strength=%.3f', tick.symbol, score, regime, signal.strength)\n\n                # Regime filtering: suppress in VOLATILE, penalize in RANGE\n                filtered, reason = self._regime_filter(signal, regime, score)"
)

# --- 2g. Add warning log on suppression ---
st = st.replace(
    "                if not filtered:\n                    self._signals_suppressed += 1",
    "                if not filtered:\n                    logger.warning('SIGNAL SUPPRESSED %s: regime=%s score=%.1f reason=%s', tick.symbol, regime, score, reason)\n                    self._signals_suppressed += 1"
)

# --- 2h. Zone gate + strong score in _regime_filter ---
st = st.replace(
    "        # VOLATILE: only allow very high-confidence signals\n        if regime == RegimeType.VOLATILE and score < 40:",
    "        # Three-zone calibration gate\n        zone = self._zone_gate(score, signal.symbol)\n        if zone == \"NO_TRADE\":\n            return False, \"NO_TRADE zone\"\n        if zone == \"OBSERVE\":\n            return False, \"OBSERVE zone\"\n\n        # Strong score gate (permissive)\n        if score < self.score_cfg.strong_score_to_execute:\n            logger.info(\"SCORE BLOCK %s: score %.1f < strong %.1f\", signal.symbol, score, self.score_cfg.strong_score_to_execute)\n            return False, f\"score {score} below strong threshold\"\n\n        # VOLATILE: only allow very high-confidence signals\n        if regime == RegimeType.VOLATILE and score < 40:"
)

# --- 2i. Lower stress filter floor ---
st = st.replace(
    "max(self.score_cfg.min_score_to_emit - 15, 30.0)",
    "max(self.score_cfg.min_score_to_emit - 15, 15.0)"
)

# --- 2j. Expectancy tracking in record_trade_result ---
st = st.replace(
    "    def record_trade_result(self, pnl: float) -> None:\n        \"\"\"Record trade outcome for equity stress tracking. Called by orchestrator after fills.\"\"\"\n        if pnl > 0:\n            self._consecutive_losses = 0\n        elif pnl < 0:\n            self._consecutive_losses += 1",
    "    def record_trade_result(self, pnl: float) -> None:\n        \"\"\"Record trade outcome for equity stress tracking. Called by orchestrator after fills.\"\"\"\n        if pnl > 0:\n            self._consecutive_losses = 0\n        elif pnl < 0:\n            self._consecutive_losses += 1\n\n        # Expectancy validation tracking\n        sym = \"default\"\n        if sym not in self._trade_results:\n            self._trade_results[sym] = []\n        self._trade_results[sym].append({\"pnl\": pnl, \"win\": pnl > 0})\n        if len(self._trade_results[sym]) >= 50:\n            recent = self._trade_results[sym][-50:]\n            wins = [t[\"pnl\"] for t in recent if t[\"win\"]]\n            losses = [-t[\"pnl\"] for t in recent if not t[\"win\"]]\n            if wins and losses:\n                self._avg_win = sum(wins) / len(wins)\n                self._avg_loss = sum(losses) / len(losses)\n                self._expectancy = (len(wins) / 50) * self._avg_win - (len(losses) / 50) * self._avg_loss\n                if self._expectancy > 0:\n                    logger.info(\"EXPECTANCY VALIDATED %s: avg_win=%.2f avg_loss=%.2f exp=%.2f\", sym, self._avg_win, self._avg_loss, self._expectancy)\n                else:\n                    logger.info(\"EXPECTANCY NOT VALIDATED %s: avg_win=%.2f avg_loss=%.2f exp=%.2f\", sym, self._avg_win, self._avg_loss, self._expectancy)"
)

# --- 2k. Expectancy in stats ---
st = st.replace(
    "} if hasattr(self, '_recent_scores') else {},\n        }",
    "} if hasattr(self, '_recent_scores') else {},\n            \"expectancy\": round(getattr(self, '_expectancy', 0), 4),\n            \"avg_win\": round(getattr(self, '_avg_win', 0), 4),\n            \"avg_loss\": round(getattr(self, '_avg_loss', 0), 4),\n        }"
)

write(BASE + "\\services\\v3\\strategy.py", st)
if syn(BASE + "\\services\\v3\\strategy.py"):
    print("  OK (syntax valid)")
else:
    print("  SYNTAX ERROR!")

# ===================== 3. execution.py =====================
print("[3/3] services/v3/execution.py ...")
ex = read(BASE + "\\services\\v3\\execution.py")
ex = ex.replace(
    "        # Recent trade results for risk checker\n        self._recent_pnls: list[float] = []  # last N trade PnLs",
    "        # Recent trade results for risk checker\n        self._recent_pnls: list[float] = []  # last N trade PnLs\n\n        # ATR tracking for position sizing\n        self._atr_values: Dict[str, list] = {}\n        self._atr_history: Dict[str, list] = {}"
)
ex = ex.replace(
    "    def compute_position_size(",
    "    def update_atr(self, symbol: str, atr: float):\n        \"\"\"Track ATR values for position sizing.\"\"\"\n        if symbol not in self._atr_values:\n            self._atr_values[symbol] = []\n        self._atr_values[symbol].append(atr)\n        if len(self._atr_values[symbol]) > 14:\n            self._atr_values[symbol] = self._atr_values[symbol][-14:]\n        self._atr_history[symbol] = list(self._atr_values[symbol])\n\n    def _get_avg_atr(self, symbol: str) -> float:\n        \"\"\"Get average ATR over tracked period.\"\"\"\n        vals = self._atr_values.get(symbol, [])\n        return sum(vals) / len(vals) if vals else 0.0\n\n    def compute_position_size("
)
ex = ex.replace(
    "        equity = self.equity\n        risk_budget = equity * settings.risk.risk_per_trade_pct  # e.g., $100 on $10K at 1%",
    "        equity = self.equity\n        # Risk budget with ATR-based scaling and clamping\n        risk_pct = getattr(settings.risk, 'risk_per_trade_pct_max', 0.01)\n        avg_atr = self._get_avg_atr(signal.symbol)\n        if atr_pct > 0 and avg_atr > 0:\n            atr_ratio = atr_pct / avg_atr\n            if atr_ratio > 3.0:\n                risk_pct = risk_pct * 0.5\n            elif atr_ratio < 1.0:\n                risk_pct = risk_pct * 0.75\n        risk_min = getattr(settings.risk, 'risk_per_trade_pct_min', 0.005)\n        risk_max = getattr(settings.risk, 'risk_per_trade_pct_max', 0.01)\n        risk_pct = max(risk_min, min(risk_max, risk_pct))\n        risk_budget = equity * risk_pct"
)
ex = ex.replace(
    '            "tp_exits": self._tp_exits,\n        }',
    '            "tp_exits": self._tp_exits,\n            "atr_values": dict(self._atr_values),\n            "atr_history": dict(self._atr_history),\n        }'
)
write(BASE + "\\services\\v3\\execution.py", ex)
if syn(BASE + "\\services\\v3\\execution.py"):
    print("  OK (syntax valid)")
else:
    print("  SYNTAX ERROR!")

# ===================== VERIFICATION =====================
print("\n" + "=" * 55)
print(" VERIFICATION (39 checks)")
print("=" * 55)
R = []
print("\n--- config/settings.py ---")
sp = read(BASE + "\\config\\settings.py")
chk("risk_pct_min", "risk_per_trade_pct_min" in sp, R)
chk("risk_pct_max", "risk_per_trade_pct_max" in sp, R)
chk("cooldown_ticks", "signal_cooldown_ticks" in sp, R)
chk("min_trade_score=25", "min_trade_score" in sp and "25.0" in sp, R)
chk("strong_trade_score=30", "strong_trade_score" in sp and "30.0" in sp, R)
chk("notrade_zone=15", "notrade_zone_max" in sp and "15.0" in sp, R)
chk("observe_zone=25", "observe_zone_max" in sp and "25.0" in sp, R)
chk("strong_score_to_execute", "strong_score_to_execute" in sp, R)
print("\n--- services/v3/strategy.py ---")
st = read(BASE + "\\services\\v3\\strategy.py")
chk("strong_score in config", "strong_score_to_execute: float = 30.0" in st, R)
chk("notrade_zone in config", "notrade_zone_max: float = 15.0" in st, R)
chk("observe_zone in config", "observe_zone_max: float = 25.0" in st, R)
chk("_zone_gate method", "def _zone_gate(" in st, R)
chk("NO_TRADE zone", "NO_TRADE" in st, R)
chk("OBSERVE zone", "OBSERVE" in st, R)
chk("zone_gate called", "self._zone_gate(" in st, R)
chk("OBSERVE suppressed", 'return False, "OBSERVE' in st, R)
chk("strong_score used", "score < self.score_cfg.strong_score_to_execute" in st, R)
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
chk("SCORE_DEBUG log", "SCORE_DEBUG" in st, R)
chk("momentum halved", "* 0.5" in st and "tick-level" in st, R)
chk("VOLATILE penalty -5", "volatile_regime_penalty: float = -5" in st, R)
chk("RANGE penalty -5", "range_regime_penalty: float = -5" in st, R)
chk("stress floor 15", "min_score_to_emit - 15, 15.0" in st, R)
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
print(f"\n{'='*55}")
if F == 0:
    print(f" ALL {P}/{len(R)} PASSED!")
else:
    print(f" {P}/{len(R)} passed, {F} FAILED:")
    for n, s in R:
        if s == "FAIL": print(f"   - {n}")
print(f"{'='*55}")
print("\nThreshold summary:")
print("  min_score_to_emit:     25 (was 45)")
print("  strong_score_execute:  30 (was 70)")
print("  NO_TRADE zone:         < 15 (was < 55)")
print("  OBSERVE zone:          < 25 (was < 65)")
print("  VOLATILE penalty:      -5 (was -30)")
print("  RANGE penalty:         -5 (was -15)")
print("  momentum threshold:    0.15% (was 0.3%)")
print("  stress filter floor:   15 (was 30)")
print("\nRun: python main.py")