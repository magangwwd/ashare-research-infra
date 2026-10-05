"""15-minute A-share ingestion and deterministic daily aggregation."""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from . import clean


def minute_window(day: date) -> tuple[str, str]:
    stamp = day.isoformat()
    return f"{stamp} 09:00:00", f"{stamp} 16:00:00"


def history_window(year: int, start: date, end: date) -> tuple[str, str]:
    left = max(start, date(year, 1, 1))
    right = min(end, date(year, 12, 31))
    return f"{left.isoformat()} 09:00:00", f"{right.isoformat()} 16:00:00"


def validate_bars(raw: pd.DataFrame, *, expected_day: date | None = None) -> pd.DataFrame:
    """Normalize and validate one bounded 15-minute response."""
    if raw.empty:
        return clean.normalize("stk_mins", raw)
    required = {"ts_code", "trade_time", "open", "high", "low", "close", "vol", "amount"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"stk_mins missing fields: {sorted(missing)}")
    normalized = clean.normalize("stk_mins", raw)
    parsed = pd.to_datetime(normalized["trade_time"], errors="raise")
    if expected_day is not None and set(parsed.dt.date) != {expected_day}:
        raise ValueError("stk_mins response crossed the requested trading day")
    if normalized.duplicated(clean.KEYS["stk_mins"]).any():
        raise ValueError("stk_mins duplicate primary keys")
    numeric = normalized[["open", "high", "low", "close", "vol", "amount"]]
    if numeric.isna().any().any():
        raise ValueError("stk_mins contains null OHLCVA values")
    if (numeric[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("stk_mins contains non-positive prices")
    if (numeric[["vol", "amount"]] < 0).any().any():
        raise ValueError("stk_mins contains negative volume or amount")
    if (normalized["freq"] != "15min").any():
        raise ValueError("stk_mins must be 15min frequency")
    return normalized.sort_values(["security_id", "trade_time"], kind="stable").reset_index(drop=True)


def aggregate_daily(minute_clean: pd.DataFrame, day: date) -> pd.DataFrame:
    """Aggregate 15-minute Clean bars to the existing raw daily field contract.

    Tushare minute volume is shares and amount is CNY. The daily API uses lots
    (100 shares) and thousand CNY, so units are converted explicitly.
    pre_close/change/pct_chg are intentionally left null: inferring the official
    ex-right adjusted pre-close from raw intraday bars would be incorrect.
    """
    if minute_clean.empty:
        return pd.DataFrame()
    frame = minute_clean.loc[minute_clean["trade_date"].eq(day)].copy()
    if frame.empty:
        return pd.DataFrame()
    frame = frame.sort_values(["ts_code", "trade_time"], kind="stable")
    rows = []
    for code, group in frame.groupby("ts_code", sort=True):
        rows.append({
            "ts_code": code,
            "trade_date": day.strftime("%Y%m%d"),
            "open": float(group.iloc[0]["open"]),
            "high": float(group["high"].max()),
            "low": float(group["low"].min()),
            "close": float(group.iloc[-1]["close"]),
            "pre_close": pd.NA,
            "change": pd.NA,
            "pct_chg": pd.NA,
            "vol": float(group["vol"].sum()) / 100.0,
            "amount": float(group["amount"].sum()) / 1000.0,
        })
    return pd.DataFrame(rows)


def covered_codes(minute_clean: pd.DataFrame, day: date) -> set[str]:
    if minute_clean.empty:
        return set()
    return set(minute_clean.loc[minute_clean["trade_date"].eq(day), "ts_code"].dropna())
