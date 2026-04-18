for path in [r"core\execution\execution_analytics.py", r"core\execution\order_router.py"]:
    print(f"\n{'#'*60}")
    print(f"# {path}")
    print(f"{'#'*60}")
    with open(path, "r", encoding="utf-8") as f:
        print(f.read())
