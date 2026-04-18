import os, re

base = os.path.dirname(os.path.abspath(__file__))

# Fix 1: portfolio_state.py - restore _get_or_create
path = os.path.join(base, "core", "portfolio", "portfolio_state.py")
with open(path, "r", encoding="utf-8") as f:
    c = f.read()
c = c.replace("if symbol not in self._positions:mbolPosition(symbol=symbol)",
    "if symbol not in self._positions:\n            self._positions[symbol] = SymbolPosition(symbol=symbol)")
with open(path, "w", encoding="utf-8") as f:
    f.write(c)
print("[OK] Fixed portfolio_state.py")

# Fix 2: correlation.py - restore _returns.clear()
path = os.path.join(base, "core", "portfolio", "correlation.py")
with open(path, "r", encoding="utf-8") as f:
    c = f.read()
c = c.replace("self._last_prices.clear()\n            self._cached_matrix = None",
    "self._returns.clear()\n            self._last_prices.clear()\n            self._cached_matrix = None")
with open(path, "w", encoding="utf-8") as f:
    f.write(c)
print("[OK] Fixed correlation.py")

# Fix 3: exposure_allocator.py - restore _is_inventory_reduction
path = os.path.join(base, "core", "portfolio", "exposure_allocator.py")
with open(path, "r", encoding="utf-8") as f:
    c = f.read()
bad = 'if side == "SELL" and pos.quantity > 0:\n        return False'
good = 'if side == "SELL" and pos.quantity > 0:\n            return True\n        return False'
c = c.replace(bad, good)
with open(path, "w", encoding="utf-8") as f:
    f.write(c)
print("[OK] Fixed exposure_allocator.py")

# Fix 4: hedge_trigger.py - remove orphaned reset lines + add proper reset
path = os.path.join(base, "core", "portfolio", "hedge_trigger.py")
with open(path, "r", encoding="utf-8") as f:
    c = f.read()
# Remove everything after get_status closing that shouldn't be there
if "'@ | python" in c:
    c = c[:c.index("'@ | python")]
# Add proper reset method if missing
if "def reset(self):" not in c:
    c = c.rstrip() + "\n\n    def reset(self):\n        self._hedge_count = 0\n        self._last_hedges = []\n"
with open(path, "w", encoding="utf-8") as f:
    f.write(c)
print("[OK] Fixed hedge_trigger.py")

# Fix 5: test_portfolio.py - verify it's complete
path = os.path.join(base, "tests", "test_portfolio.py")
with open(path, "r", encoding="utf-8") as f:
    c = f.read()
if "sys.argv[1]" not in c and "sys.stdin.read()" not in c:
    print("[OK] test_portfolio.py looks good")
else:
    # Remove any trailing pipe garbage
    if "'@ | python" in c:
        c = c[:c.index("'@ | python")]
    with open(path, "w", encoding="utf-8") as f:
        f.write(c)
    print("[OK] Cleaned test_portfolio.py")

print("\n=== All fixes applied. Now run: python -m pytest tests/test_portfolio.py -v ===")
