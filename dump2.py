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

dump("config/settings.py", [(20, 60)])

dump("services/v3/strategy.py", [
    (30, 100),   # SignalScoreConfig through BaseStrategy
    (110, 200),  # V3StrategyService __init__ and on_tick
    (320, 350),  # _regime_filter
    (405, 460),  # record_trade_result and stats
])

dump("services/v3/execution.py", [(60, 100), (170, 230)])