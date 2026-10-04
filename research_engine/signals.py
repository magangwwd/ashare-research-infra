"""Signal-to-target-weight helpers."""
from __future__ import annotations

import pandas as pd


def rank_long_only(
    frame: pd.DataFrame,
    factor: str,
    *,
    top_n: int = 10,
    ascending: bool = False,
    gross_weight: float = 1.0,
) -> pd.DataFrame:
    """Equal-weight the top N securities on each signal date.

    The output uses signal_date rather than execution_date.  The backtester is
    responsible for moving these weights to the next market session.
    """
    if top_n <= 0:
        raise ValueError("top_n must be positive")
    if not 0 < gross_weight <= 1:
        raise ValueError("gross_weight must be in (0, 1]")
    required = {"trade_date", "security_id", factor}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"missing signal inputs: {sorted(missing)}")

    eligible = frame.loc[frame[factor].notna(), ["trade_date", "security_id", factor]].copy()
    eligible = eligible.sort_values(
        ["trade_date", factor, "security_id"],
        ascending=[True, ascending, True],
        kind="stable",
    )
    selected = eligible.groupby("trade_date", sort=True).head(top_n).copy()
    counts = selected.groupby("trade_date")["security_id"].transform("size")
    selected["target_weight"] = gross_weight / counts
    return selected.rename(columns={"trade_date": "signal_date"})[
        ["signal_date", "security_id", "target_weight", factor]
    ].reset_index(drop=True)
