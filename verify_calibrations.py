import os, sys, ast

BASE = os.path.dirname(os.path.abspath(__file__))
PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []

def check(name, condition, detail=""):
    status = PASS if condition else FAIL
    results.append((name, condition, detail))
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail and not condition else ""))

def read(fpath):
    with open(fpath, "r", encoding="utf-8") as f:
        return f.read()

def has(src, text):
    return text in src

print("=" * 50)
print(" v3.2.1 Calibration Verification")
print("=" * 50)

print("\n--- config/settings.py ---")
fpath = os.path.join(BASE, "config", "settings.py")
src = read(fpath)
check("risk_pct_min", has(src, "risk_per_trade_pct_min"))
check("risk_pct_max", has(src, "risk_per_trade_pct_max"))
check("cooldown_ticks", has(src, "cooldown_ticks: int = 10"))
check("min_trade_score=60", has(src, "min_trade_score: float = 60.0"))
check("strong_trade_score=70", has(src, "strong_trade_score: float = 70.0"))
check("notrade_zone=55", has(src, "notrade_zone_max: float = 55.0"))
check("observe_zone=65", has(src, "observe_zone_max: float = 65.0"))
check("strong_score_to_execute", has(src, "strong_score_to_execute: float = 70.0"))

print("\n--- services/v3/strategy.py ---")
fpath = os.path.join(BASE, "services", "v3", "strategy.py")
src = read(fpath)
check("strong_score in config", has(src, "strong_score_to_execute: float = 70.0"))
check("notrade_zone in config", has(src, "notrade_zone_max: float = 55.0"))
check("observe_zone in config", has(src, "observe_zone_max: float = 65.0"))
check("_zone_gate method", has(src, "def _zone_gate(self, score):"))
check("NO_TRADE zone", has(src, "return 'NO_TRADE'"))
check("OBSERVE zone", has(src, "return 'OBSERVE'"))
check("zone_gate called", has(src, "zone, zone_reason = self._zone_gate(score)"))
check("OBSERVE suppressed", has(src, "OBSERVE %s %s"))
check("strong_score used", has(src, "if stress_adjusted_score < self.score_cfg.strong_score_to_execute:"))
check("SCORE BLOCK log", has(src, "SCORE BLOCK"))
check("_tick_counter", has(src, "self._tick_counter = {}"))
check("_last_signal_tick", has(src, "self._last_signal_tick = {}"))
check("tick cooldown gate", has(src, "if ticks_since < self._cooldown_ticks:"))
check("tick counter increment", has(src, "self._tick_counter[tick.symbol] += 1"))
check("_trade_results", has(src, "self._trade_results = []"))
check("_avg_win/_avg_loss", has(src, "self._avg_win = 0.0") and has(src, "self._avg_loss = 0.0"))
check("expectancy append", has(src, "self._trade_results.append((pnl, is_win))"))
check("50-trade gate", has(src, "if len(self._trade_results) >= 50"))
check("EXPECTANCY VALIDATED", has(src, "EXPECTANCY VALIDATED"))
check("EXPECTANCY NOT VALIDATED", has(src, "EXPECTANCY NOT VALIDATED"))
check("expectancy in stats", has(src, '"expectancy_validated": self._expectancy_validated'))
check("avg_win in stats", has(src, '"avg_win": round(self._avg_win'))
try:
    ast.parse(src)
    check("syntax valid", True)
except SyntaxError as e:
    check("syntax valid", False, str(e))

print("\n--- services/v3/execution.py ---")
fpath = os.path.join(BASE, "services", "v3", "execution.py")
src = read(fpath)
check("_atr_values", has(src, "self._atr_values = {}"))
check("_atr_history", has(src, "self._atr_history = {}"))
check("update_atr method", has(src, "def update_atr(self, symbol, atr):"))
check("_get_avg_atr method", has(src, "def _get_avg_atr(self, symbol):"))
check("atr_ratio scaling", has(src, "atr_ratio = atr / avg_atr"))
check("risk clamping", has(src, "risk_pct = max(risk_min, min(risk_pct, risk_max))"))
check("atr in stats", has(src, '"atr_values":'))
try:
    ast.parse(src)
    check("syntax valid", True)
except SyntaxError as e:
    check("syntax valid", False, str(e))

print("\n" + "=" * 50)
total = len(results)
passed = sum(1 for _, ok, _ in results if ok)
failed = total - passed
print(f" RESULTS: {passed}/{total} passed")
if failed > 0:
    print(f" {failed} FAILED:")
    for name, ok, detail in results:
        if not ok:
            print(f"   - {name}" + (f" ({detail})" if detail else ""))
print("=" * 50)

if failed == 0:
    print(" ALL CALIBRATION CHANGES VERIFIED")
    sys.exit(0)
else:
    print(f" {failed} FAILED")
    sys.exit(1)