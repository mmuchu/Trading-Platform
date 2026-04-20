import os, sys, shutil

BASE = os.path.dirname(os.path.abspath(__file__))
orch = os.path.join(BASE, "orchestrator_v3.py")

print("[1/2] Reading orchestrator_v3.py ...")
for enc in ("utf-8-sig", "utf-8", "latin-1"):
    try:
        with open(orch, "r", encoding=enc) as f:
            content = f.read()
        break
    except Exception:
        continue

print("[2/2] Patching signal handling ...")

old = """        _signal.signal(_signal.SIGINT, _shutdown)
        _signal.signal(_signal.SIGTERM, _shutdown)"""

new = """        try:
            _signal.signal(_signal.SIGINT, _shutdown)
            _signal.signal(_signal.SIGTERM, _shutdown)
        except (ValueError, OSError):
            pass  # signal not available in non-main thread"""

if old in content:
    content = content.replace(old, new)
    with open(orch, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    print("  [OK] Patched signal handling")
else:
    print("  [WARN] Signal block not found - checking if already patched...")
    if "try:" in content and "signal not available" in content:
        print("  [OK] Already patched")
    else:
        print("  [FAIL] Cannot find signal block to patch")
        sys.exit(1)

pc = os.path.join(BASE, "__pycache__")
if os.path.isdir(pc):
    shutil.rmtree(pc)
pc2 = os.path.join(BASE, "services", "v3", "__pycache__")
if os.path.isdir(pc2):
    shutil.rmtree(pc2)

print("\n=== DONE ===")
print("Next: python main.py --mode v3")