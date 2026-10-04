"""Simple engineering factors used to validate the research pipeline."""
from __future__ import annotations

import numpy as np
import pandas as pd


def add_engineering_factors(
    frame: pd.DataFrame,
    *,
    momentum_windows: tuple[int, ...] = (20, 60),
    volatility_window: int = 20,
    volume_window: int = 20,
) -> pd.DataFrame:
    """Add backward-looking factors only.

    These are plumbing tests, not claims that the factors have predictive value.
    Values on date t use information available no later than date t and are meant
    to be executed no earlier than the next trading session.
    """
    required = {"security_id", "trade_date", "adj_close", "vol"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"missing factor inputs: {sorted(missing)}")
    if volatility_window < 2 or volume_window < 2 or any(w < 1 for w in momentum_windows):
        raise ValueError("factor windows must be positive and rolling windows >= 2")

    result = frame.sort_values(["security_id", "trade_date"], kind="stable").copy()
    groups = result.groupby("security_id", sort=False)
    for window in momentum_windows:
        result[f"momentum_{window}d"] = groups["adj_close"].pct_change(window, fill_method=None)

    daily_return = groups["adj_close"].pct_change(fill_method=None)
    result[f"volatility_{volatility_window}d"] = (
        daily_return.groupby(result["security_id"], sort=False)
        .rolling(volatility_window, min_periods=volatility_window)
        .std(ddof=1)
        .reset_index(level=0, drop=True)
        * np.sqrt(252.0)
    )
    result[f"volume_ratio_{volume_window}d"] = (
        result["vol"]
        / groups["vol"].transform(
            lambda s: s.rolling(volume_window, min_periods=volume_window).mean()
        )
    )
    return result.sort_values(["trade_date", "security_id"], kind="stable").reset_index(drop=True)
