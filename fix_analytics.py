path = r"core\execution\execution_analytics.py"
with open(path, "r", encoding="utf-8") as f:
    lines = f.readlines()

new_lines = []
fixed = False
for i, line in enumerate(lines):
    new_lines.append(line)
    if "bd[r.algorithm] = bd.get(r.algorithm, 0) + 1" in line:
        if i + 1 < len(lines) and "def reset" in lines[i + 1]:
            new_lines.append("        return bd\n")
            fixed = True

if fixed:
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)
    print("[OK] Fixed missing return in _get_algorithm_breakdown")
else:
    print("[SKIP] No fix needed")
