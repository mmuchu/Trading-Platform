import os, sys, zlib, base64, hashlib, shutil

BASE = os.path.dirname(os.path.abspath(__file__))
RG = os.path.join(BASE, "risk_guard")

print("[1/2] Generating signal_validator.py ...")
B64 = (
"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"
)
raw = zlib.decompress(base64.b64decode(B64))
md5 = hashlib.md5(raw).hexdigest()
if md5 != "d714257c17883db7010c8a7bbaff799e":
    print(f"  [FAIL] MD5 mismatch: {md5}")
    sys.exit(1)
p = os.path.join(RG, "signal_validator.py")
with open(p, "wb") as f:
    f.write(raw)
print(f"  [OK] signal_validator.py ({len(raw)} bytes, md5={md5})")

print("[2/2] Verifying ...")
pc = os.path.join(RG, "__pycache__")
if os.path.isdir(pc):
    shutil.rmtree(pc)
try:
    from risk_guard.signal_validator import SignalValidator
    sv = SignalValidator()
    sv.update_price("BTCUSDT", 50000.0)
    print("  [OK] SignalValidator.update_price works!")
except Exception as e:
    print(f"  [FAIL] {e}")
print("\n=== DONE ===")
print("Next: python main.py --mode v3")