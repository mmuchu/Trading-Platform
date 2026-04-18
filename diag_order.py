import re, os

path = r"core\execution\order.py"
print(f"{'#'*60}")
print(f"# {path}")
print(f"{'#'*60}")
if os.path.exists(path):
    with open(path, "r", encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            s = line.strip()
            if re.match(r'^(class |from |import )', s):
                print(f"  L{i}: {s}")
else:
    print("  [NOT FOUND]")
