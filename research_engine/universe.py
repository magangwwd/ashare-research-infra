"""Historical universe rules that avoid current-list survivorship filtering."""
from __future__ import annotations

import pandas as pd


def filter_historical_universe(
    market: pd.DataFrame,
    master: pd.DataFrame,
    *,
    boards: tuple[str, ...] = ("MAIN",),
    exchanges: tuple[str, ...] = ("SSE", "SZSE"),
    min_listing_days: int = 0,
) -> pd.DataFrame:
    """Filter rows using listing/delisting dates as they apply on each trade date.

    listing_status is intentionally not used: a stock that is delisted today can
    still be a valid member of a historical backtest universe before delisting.
    ST and other daily eligibility rules are separate Task 02 state layers and
    are not guessed when the source is unavailable.
    """
    if min_listing_days < 0:
        raise ValueError("min_listing_days must be nonnegative")
    cols = ["security_id", "exchange", "board", "security_type", "list_date", "delist_date"]
    missing = set(cols) - set(master.columns)
    if missing:
        raise ValueError(f"master missing universe fields: {sorted(missing)}")
    merged = market.merge(master[cols], on="security_id", how="left", validate="many_to_one")
    td = pd.to_datetime(merged["trade_date"])
    listed = pd.to_datetime(merged["list_date"])
    delisted = pd.to_datetime(merged["delist_date"])
    eligible = (
        merged["exchange"].isin(exchanges)
        & merged["board"].isin(boards)
        & merged["security_type"].eq("A_SHARE")
        & listed.notna()
        & (listed <= td)
        & (delisted.isna() | (delisted > td))
    )
    if min_listing_days:
        eligible &= (td - listed).dt.days >= min_listing_days
    return merged.loc[eligible].sort_values(["trade_date", "security_id"], kind="stable").reset_index(drop=True)
