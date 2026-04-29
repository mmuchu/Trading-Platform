BASE = r"C:\dev\trading-automation-system\trading-platform"

def dump(path, ranges):
    with open(f"{BASE}\\{path}", "r", encoding="utf-8") as f:
        lines = f.readlines()
    print(f"\n{'='*60}")
    print(f" {path}")
    print(f"{'='*60}")
    for start, end in ranges:
        print(f"\n--- Lines {start}-{end} ---")
        for i in range(start-1, min(end, len(lines))):
            print(f"  L{i+1:4d}: {lines[i].rstrip()}")

dump("config/settings.py", [(20, 70)])

dump("services/v3/strategy.py", [
    (30, 55),
    (95, 120),
    (120, 155),
    (175, 200),
    (320, 360),
    (405, 445),
])

dump("services/v3/execution.py", [(170, 235)])