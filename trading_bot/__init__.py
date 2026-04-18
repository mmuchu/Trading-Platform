"""
Trading Bot v1.0 - Production Baseline
=======================================
Clean, stable, synchronous trading system.
No async complexity. No unstable dependencies.
Just: Feed -> Strategy -> Risk -> Execution -> Log -> Dashboard

Usage:
    python -m trading_bot                  # Live paper trading
    python -m trading_bot --backtest       # Backtest mode
    python -m trading_bot --dashboard      # With web dashboard
    python -m trading_bot --backtest --dashboard  # Backtest + dashboard
"""
