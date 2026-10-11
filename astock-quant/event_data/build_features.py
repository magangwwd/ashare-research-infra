from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "event_data"
DAILY_DATA_DIR = BASE_DIR / "daily_data"
ANNOUNCEMENTS_CSV = DATA_DIR / "announcements.csv"
RESEARCH_CSV = DATA_DIR / "research_reports.csv"
NEWS_CSV = DATA_DIR / "stock_news.csv"
DEFAULT_OUTPUT = DATA_DIR / "daily_event_features.csv"

EVENT_FEATURE_COLUMNS = [
    "ann_count_5d",
    "ann_count_20d",
    "ann_risk_count_20d",
    "ann_positive_count_20d",
    "news_count_5d",
    "news_count_20d",
    "news_risk_count_20d",
    "news_positive_count_20d",
    "research_count_20d",
    "research_count_60d",
    "research_buy_count_60d",
]


def _read_events(path: Path, kind: str) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=["ts_code", "event_date", "kind"])
    df = pd.read_csv(path, dtype=str)
    if df.empty:
        return pd.DataFrame(columns=["ts_code", "event_date", "kind"])
    out = pd.DataFrame()
    out["ts_code"] = df["ts_code"].astype(str)
    out["event_date"] = pd.to_datetime(df["event_date"], errors="coerce")
    out["kind"] = kind
    out["is_risk"] = _numeric_col(df, "is_risk")
    out["is_positive"] = _numeric_col(df, "is_positive")
    out["is_buy_rating"] = _numeric_col(df, "is_buy_rating")
    return out.dropna(subset=["event_date", "ts_code"])


def _numeric_col(df: pd.DataFrame, column: str) -> pd.Series:
    if column in df.columns:
        values = df[column]
    else:
        values = pd.Series(0, index=df.index)
    return pd.to_numeric(values, errors="coerce").fillna(0).astype(int)


def _load_all_events() -> pd.DataFrame:
    frames = [
        _read_events(ANNOUNCEMENTS_CSV, "ann"),
        _read_events(RESEARCH_CSV, "research"),
        _read_events(NEWS_CSV, "news"),
    ]
    events = pd.concat(frames, ignore_index=True)
    return events.dropna(subset=["event_date", "ts_code"]) if not events.empty else events


def _load_trading_dates(ts_code: str) -> pd.DatetimeIndex:
    path = DAILY_DATA_DIR / f"{ts_code}.csv"
    if not path.exists():
        return pd.DatetimeIndex([])
    try:
        df = pd.read_csv(path, usecols=["Date"])
    except Exception:
        return pd.DatetimeIndex([])
    return pd.DatetimeIndex(pd.to_datetime(df["Date"], errors="coerce").dropna()).sort_values()


def _assign_visibility_dates(events: pd.DataFrame, trading_dates: pd.DatetimeIndex, lag_days: int) -> pd.Series:
    if events.empty or len(trading_dates) == 0:
        return pd.Series(pd.NaT, index=events.index, dtype="datetime64[ns]")
    visible_after = events["event_date"] + pd.to_timedelta(lag_days, unit="D")
    positions = np.searchsorted(trading_dates.values, visible_after.values, side="left")
    assigned = np.full(len(events), np.datetime64("NaT"), dtype="datetime64[ns]")
    valid = positions < len(trading_dates)
    assigned[valid] = trading_dates.values[positions[valid]]
    return pd.Series(pd.DatetimeIndex(assigned), index=events.index)


def _build_one_code(ts_code: str, events: pd.DataFrame, visibility_lag_days: int) -> pd.DataFrame:
    trading_dates = _load_trading_dates(ts_code)
    if len(trading_dates) == 0:
        return pd.DataFrame()
    events = events.copy()
    events["visible_date"] = _assign_visibility_dates(events, trading_dates, visibility_lag_days)
    events = events.dropna(subset=["visible_date"])

    daily = pd.DataFrame(index=trading_dates)
    base_cols = [
        "ann_count",
        "ann_risk_count",
        "ann_positive_count",
        "news_count",
        "news_risk_count",
        "news_positive_count",
        "research_count",
        "research_buy_count",
    ]
    for col in base_cols:
        daily[col] = 0.0

    for date, frame in events.groupby("visible_date"):
        kind = frame["kind"]
        daily.at[date, "ann_count"] = float((kind == "ann").sum())
        daily.at[date, "ann_risk_count"] = float(frame.loc[kind == "ann", "is_risk"].sum())
        daily.at[date, "ann_positive_count"] = float(frame.loc[kind == "ann", "is_positive"].sum())
        daily.at[date, "news_count"] = float((kind == "news").sum())
        daily.at[date, "news_risk_count"] = float(frame.loc[kind == "news", "is_risk"].sum())
        daily.at[date, "news_positive_count"] = float(frame.loc[kind == "news", "is_positive"].sum())
        daily.at[date, "research_count"] = float((kind == "research").sum())
        daily.at[date, "research_buy_count"] = float(frame.loc[kind == "research", "is_buy_rating"].sum())

    out = pd.DataFrame(index=trading_dates)
    out["ann_count_5d"] = daily["ann_count"].rolling(5, min_periods=1).sum()
    out["ann_count_20d"] = daily["ann_count"].rolling(20, min_periods=1).sum()
    out["ann_risk_count_20d"] = daily["ann_risk_count"].rolling(20, min_periods=1).sum()
    out["ann_positive_count_20d"] = daily["ann_positive_count"].rolling(20, min_periods=1).sum()
    out["news_count_5d"] = daily["news_count"].rolling(5, min_periods=1).sum()
    out["news_count_20d"] = daily["news_count"].rolling(20, min_periods=1).sum()
    out["news_risk_count_20d"] = daily["news_risk_count"].rolling(20, min_periods=1).sum()
    out["news_positive_count_20d"] = daily["news_positive_count"].rolling(20, min_periods=1).sum()
    out["research_count_20d"] = daily["research_count"].rolling(20, min_periods=1).sum()
    out["research_count_60d"] = daily["research_count"].rolling(60, min_periods=1).sum()
    out["research_buy_count_60d"] = daily["research_buy_count"].rolling(60, min_periods=1).sum()
    out["Date"] = out.index.strftime("%Y-%m-%d")
    out["ts_code"] = ts_code
    return out[["Date", "ts_code"] + EVENT_FEATURE_COLUMNS]


def build_event_features(output_path: Path, visibility_lag_days: int = 1) -> pd.DataFrame:
    events = _load_all_events()
    if events.empty:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        empty = pd.DataFrame(columns=["Date", "ts_code"] + EVENT_FEATURE_COLUMNS)
        empty.to_csv(output_path, index=False, encoding="utf-8-sig")
        print(f"[event_features] no events; wrote empty schema -> {output_path}", flush=True)
        return empty

    parts: list[pd.DataFrame] = []
    for ts_code, frame in events.groupby("ts_code", sort=True):
        part = _build_one_code(str(ts_code), frame, visibility_lag_days)
        if not part.empty:
            parts.append(part)
    result = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["Date", "ts_code"] + EVENT_FEATURE_COLUMNS)
    result = result.sort_values(["Date", "ts_code"]).reset_index(drop=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(output_path, index=False, encoding="utf-8-sig")
    print(f"[event_features] saved {len(result)} rows -> {output_path}", flush=True)
    return result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="将事件元数据聚合为日频滚动特征")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--visibility-lag-days", type=int, default=1, help="事件可见性滞后自然日，默认 1")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    build_event_features(args.output, visibility_lag_days=args.visibility_lag_days)


if __name__ == "__main__":
    main()
