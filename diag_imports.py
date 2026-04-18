import re, os

for path in [r"core\execution\execution_analytics.py", r"core\execution\order_router.py"]:
    print(f"\n{'='*50}")
    print(f"FILE: {path}")
    print(f"{'='*50}")
    if not os.path.exists(path):
        print("  [NOT FOUND]")
        continue
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            s = line.strip()
            if re.match(r'^(class |def )', s):
                print(f"  L{i}: {s}")
