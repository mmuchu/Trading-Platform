path = "config\settings.py"
with open(path, "r", encoding="utf-8") as f:
    c = f.read()
if "OrderRouterConfig" not in c:
    insert = '''@dataclass
class OrderRouterConfig:
    """Smart Order Router configuration."""
    default_algorithm: str = "slice"
    max_child_orders: int = 10
    twap_duration_secs: float = 300.0
    twap_interval_secs: float = 30.0
    vwap_duration_secs: float = 600.0
    participation_rate_max: float = 0.1
    cancel_on_drawdown_pct: float = 0.5

'''
    c = c.replace("@dataclass\nclass DashboardConfig:", insert + "@dataclass\nclass DashboardConfig:")
    c = c.replace("    dashboard: DashboardConfig", "    order_router: OrderRouterConfig = field(default_factory=OrderRouterConfig)\n    dashboard: DashboardConfig")
    with open(path, "w", encoding="utf-8") as f:
        f.write(c)
    print("[OK] Added OrderRouterConfig to settings.py")
else:
    print("[OK] OrderRouterConfig already exists")
