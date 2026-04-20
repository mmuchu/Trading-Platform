import base64, zipfile, io, os, sys
base = os.path.dirname(os.path.abspath(__file__))
b64file = os.path.join(base, "v31.b64")
if not os.path.exists(b64file):
    print(f"[FAIL] {b64file} not found")
    sys.exit(1)
with open(b64file, "r") as f:
    b64 = f.read().replace("\n", "").strip()
try:
    raw = base64.b64decode(b64)
    z = zipfile.ZipFile(io.BytesIO(raw))
    names = z.namelist()
    z.extractall(base)
    z.close()
    print(f"[OK] v3.1 deployed - {len(names)} files extracted:")
    for n in names:
        print(f"  + {n}")
except Exception as e:
    print(f"[FAIL] decode error: {e}")
    sys.exit(1)