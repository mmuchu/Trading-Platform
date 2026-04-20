import os
p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
t = open(p, encoding="utf-8-sig").read()
t = t.replace(
    'uvicorn.Server(cfg).serve()',
    'uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")'
)
open(p, "w", encoding="utf-8", newline="\n").write(t)
print("[OK] main.py patched")