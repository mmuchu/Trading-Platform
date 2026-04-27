p = r"C:\dev\trading-automation-system\trading-platform\orchestrator_v3.py"
c = open(p, "r", encoding="utf-8").read()
old = "try:`n                _signal.signal(_signal.SIGINT, _shutdown)`n            except (ValueError, OSError):`n                pass  # not main thread"
new = "try:\n                _signal.signal(_signal.SIGINT, _shutdown)\n            except (ValueError, OSError):\n                pass  # not main thread"
if old in c:
    c = c.replace(old, new)
    open(p, "w", encoding="utf-8").write(c)
    print("fixed")
else:
    print("old string not found")
