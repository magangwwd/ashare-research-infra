"""Backtest summary metrics; interpretation requires adequate history."""
from __future__ import annotations

import math

import pandas as pd


def summarize(nav: pd.DataFrame, periods_per_year: int = 252) -> dict:
    if nav.empty or "nav" not in nav or "return" not in nav:
        raise ValueError("nav frame must contain nav and return")
    returns = pd.to_numeric(nav["return"], errors="raise").fillna(0.0)
    n = max(len(returns) - 1, 1)
    total = float(nav["nav"].iloc[-1] / nav["nav"].iloc[0] - 1) if len(nav) > 1 else 0.0
    annualized = (1 + total) ** (periods_per_year / n) - 1 if 1 + total > 0 else -1.0
    vol = float(returns.std(ddof=1) * math.sqrt(periods_per_year)) if len(returns) > 1 else 0.0
    sharpe = float(returns.mean() / returns.std(ddof=1) * math.sqrt(periods_per_year)) if len(returns) > 1 and returns.std(ddof=1) > 0 else 0.0
    peak = nav["nav"].cummax()
    drawdown = nav["nav"] / peak - 1
    return {
        "sessions": int(len(nav)),
        "total_return": total,
        "annualized_return": float(annualized),
        "annualized_volatility": vol,
        "sharpe_zero_rf": sharpe,
        "max_drawdown": float(drawdown.min()),
        "average_turnover": float(nav["turnover"].mean()) if "turnover" in nav else None,
        "total_cost": float(nav["cost"].sum()) if "cost" in nav else None,
    }
