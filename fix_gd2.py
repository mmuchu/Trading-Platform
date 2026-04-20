import os
print("fix_gd2 starting...")
p = os.path.join("C:" + os.sep + "dev" + os.sep + "trading-automation-system" + os.sep + "trading-platform", "risk_guard", "guard.py")
t = open(p).read()
t = t.replace("stale_threshold_s=stale_threshold_sec", "stale_threshold_sec=stale_threshold_sec")
open(p, "w").write(t)
print("[OK] guard.py patched")