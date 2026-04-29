BASE = r"C:\dev\trading-automation-system\trading-platform"
with open(BASE + r"\core\risk\risk_engine.py", "r", encoding="utf-8") as f:
    lines = f.readlines()
print(f"Total: {len(lines)} lines\n")
for i, line in enumerate(lines, 1):
    print(f"  L{i:3d}: {line.rstrip()}")