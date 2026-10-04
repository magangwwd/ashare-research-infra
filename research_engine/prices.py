"""Point-in-time-safe adjusted-price helpers."""
from __future__ import annotations

import pandas as pd


PRICE_COLUMNS = ("open", "high", "low", "close", "pre_close")


def add_adjusted_prices(frame: pd.DataFrame) -> pd.DataFrame:
    """Add split/dividend-adjusted OHLC without future normalization.

    Tushare adj_factor is used multiplicatively.  We intentionally do NOT divide
    by the last factor in the sample (common QFQ presentation), because that
    would make the displayed historical price depend on a future endpoint.
    Absolute levels are arbitrary; ratios/returns are the intended use.
    """
    required = {"security_id", "trade_date", "adj_factor", *PRICE_COLUMNS}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"missing adjusted-price inputs: {sorted(missing)}")
    result = frame.copy()
    if (pd.to_numeric(result["adj_factor"], errors="coerce") <= 0).any():
        raise ValueError("adj_factor must be positive")
    for column in PRICE_COLUMNS:
        result[f"adj_{column}"] = (
            pd.to_numeric(result[column], errors="raise")
            * pd.to_numeric(result["adj_factor"], errors="raise")
        )
    result = result.sort_values(["security_id", "trade_date"], kind="stable")
    result["adj_close_return"] = result.groupby("security_id", sort=False)["adj_close"].pct_change(fill_method=None)
    return result.sort_values(["trade_date", "security_id"], kind="stable").reset_index(drop=True)
