import os, sys, zlib, base64, hashlib, shutil

BASE = os.path.dirname(os.path.abspath(__file__))
RG = os.path.join(BASE, "risk_guard")

# 1. Generate correct feed_monitor.py
print("[1/3] Generating feed_monitor.py ...")
B64 = (
"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"
"ua9cJOYNzKSHseUFMaDvc5d8C/1xHED"
)
raw = zlib.decompress(base64.b64decode(B64))
md5 = hashlib.md5(raw).hexdigest()
if md5 != "3c2f15233a0d32edb1c94b91e673afa5":
    print(f"  [FAIL] MD5 mismatch: {md5}")
    sys.exit(1)
p = os.path.join(RG, "feed_monitor.py")
with open(p, "wb") as f:
    f.write(raw)
print(f"  [OK] feed_monitor.py ({len(raw)} bytes, md5={md5})")

# 2. Fix guard.py
print("[2/3] Fixing guard.py ...")
gd = os.path.join(RG, "guard.py")
content = None
for enc in ("utf-8-sig", "utf-8", "latin-1"):
    try:
        with open(gd, "r", encoding=enc) as f:
            content = f.read()
        break
    except Exception:
        continue
if content is None:
    print("  [FAIL] Cannot read guard.py")
    sys.exit(1)
fixed = content.replace(
    "stale_threshold_s=stale_threshold_sec",
    "stale_threshold_sec=stale_threshold_sec",
)
if fixed == content:
    print("  [OK] guard.py - no changes needed")
else:
    with open(gd, "w", encoding="utf-8", newline="\n") as f:
        f.write(fixed)
    print("  [OK] guard.py - fixed")

# 3. Verify
print("[3/3] Verifying ...")
pc = os.path.join(RG, "__pycache__")
if os.path.isdir(pc):
    shutil.rmtree(pc)
for d in ["__pycache__","core/__pycache__","core/v3/__pycache__",
          "services/__pycache__","services/v3/__pycache__"]:
    dp = os.path.join(BASE, d)
    if os.path.isdir(dp):
        shutil.rmtree(dp)
try:
    from risk_guard.feed_monitor import FeedMonitor
    import inspect
    sig = inspect.signature(FeedMonitor.__init__)
    params = list(sig.parameters.keys())
    print(f"  FeedMonitor params: {params}")
    exp = ["self","bus","stale_threshold_sec","gap_threshold_sec"]
    if params == exp:
        print("  [OK] Signature matches!")
    else:
        print(f"  [WARN] Expected: {exp}")
except Exception as e:
    print(f"  [FAIL] FeedMonitor: {e}")
try:
    from risk_guard.guard import PortfolioRiskGuard
    print("  [OK] PortfolioRiskGuard imported")
except Exception as e:
    print(f"  [FAIL] Guard: {e}")
print("\n=== DONE ===")
print("Next: python main.py --mode v3")