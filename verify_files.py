import hashlib, os

base = os.path.dirname(os.path.abspath(__file__))

expected = {
    "risk_guard/__init__.py":    "05cdf3c45299088bd07a4e08c56513b2",
    "risk_guard/guard.py":       "5f4e3a5324e901ce1c22481b48337db0",
    "risk_guard/feed_monitor.py":"3c2f15233a0d32edb1c94b91e673afa5",
    "risk_guard/signal_validator.py": "d714257c17883db7010c8a7bbaff799e",
    "risk_guard/risk_checker.py":"87963cf4ac484ac4a088c72aab21b775",
    "risk_guard/position_sync.py": "69d144732b5e9a82476a9ab2a0e088df",
    "risk_guard/cooldown_manager.py": "4b5a4e76d7217d0637f5f3adeec8cea3",
    "risk_guard/sl_tp_manager.py": "6a154057386a40d73afe319604cd4909",
    "orchestrator_v3.py":        "d3d0b8fa4eb9d940116c62261eda4814",
    "services/v3/execution.py":  "b89de84a75ae5b2c8488d96adc284312",
    "services/v3/market_data.py":"d13ec81b48ff18980870c686c3dc0f86",
    "config/settings.py":        "f2e6d68827c73ccbc1f5cb1e9aad1270",
    "dashboard/app_v3.py":       "1d170049f01ca496a7ac14cafe877385",
    "main.py":                   "7f9ba6e9594b8c0a5e994653ce867d3d",
}

ok = 0
fail = 0
for f, exp in expected.items():
    path = os.path.join(base, f)
    if not os.path.exists(path):
        print(f"[MISSING] {f}")
        fail += 1
        continue
    with open(path, "rb") as fh:
        got = hashlib.md5(fh.read()).hexdigest()
    sz = os.path.getsize(path)
    if got == exp:
        print(f"[OK]     {sz:>6d}  {f}")
        ok += 1
    else:
        print(f"[MISMATCH] {f}\n  expected: {exp}\n  got:      {got}")
        fail += 1

print(f"\n{'='*40}")
print(f"Result: {ok} OK, {fail} FAILED out of {ok+fail} files")