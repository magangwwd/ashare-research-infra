"""Read-only Baostock 600351.SH 15-minute evidence. Never modifies trades."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

SOURCE_VERSION = "BAOSTOCK_600351_15M_PROBE_V1"
SOURCE_SYMBOL = "600351.SH"
EXPECTED = ("09:45", "10:00", "10:15", "10:30", "10:45", "11:00", "11:15", "11:30",
            "13:15", "13:30", "13:45", "14:00", "14:15", "14:30", "14:45", "15:00")
REQUIRED = {"ts_code", "trade_time", "open", "high", "low", "close", "vol", "amount"}


def validate_one_day(bars: pd.DataFrame, daily_close_raw: float) -> dict:
    """Only complete, previously closed, exchange-standard minute sessions qualify."""
    if not REQUIRED.issubset(bars):
        raise ValueError("15m columns missing: " + str(sorted(REQUIRED-set(bars))))
    b = bars.copy()
    b["trade_time"] = pd.to_datetime(b.trade_time, errors="raise")
    if b.trade_time.dt.tz is not None:
        raise ValueError("15m timestamps must be naive Asia/Shanghai wall time")
    if (len(b) != 16 or b.trade_time.dt.strftime("%H:%M").tolist() != list(EXPECTED)
            or b.trade_time.duplicated().any()
            or b.trade_time.dt.normalize().nunique() != 1):
        raise ValueError("Not exactly sixteen sequential regular-session 15m bars")
    for field in ("open", "high", "low", "close", "vol", "amount"):
        b[field] = pd.to_numeric(b[field], errors="raise")
    import numpy as np
    if (not np.isfinite(b[["open","high","low","close","vol","amount"]]).all().all()
            or not b[["open","high","low","close"]].gt(0).all().all()
            or not b[["vol","amount"]].ge(0).all().all()
            or b.vol.sum() <= 0
            or not b.high.ge(b[["open","low","close"]].max(axis=1)).all()
            or not b.low.le(b[["open","high","close"]].min(axis=1)).all()):
        raise ValueError("Invalid 15m OHLCV")
    if not (daily_close_raw > 0) or abs(float(b.close.iloc[-1])-daily_close_raw) > max(.011, daily_close_raw*.001):
        raise ValueError("15:00 price does not match independent unadjusted Gold close")
    fraction=float(b.vol.iloc[-4:].sum()/b.vol.sum())
    return {"bar_count":16, "afternoon_vol_fraction":round(float(b.vol.iloc[8:].sum()/b.vol.sum()),6),
            "last_hour_vol_fraction":round(fraction,6), "last_hour_ge30_shadow":bool(fraction >= .30)}


def audit_source(root: Path, daily: pd.DataFrame, signals: dict, trades: list[dict],
                 start: str | None = None) -> dict:
    """Hash-verifies all partitions; returns shadow-only single-symbol research audit."""
    metadata=(root/"provider_manifest.json").read_bytes()
    source=json.loads(metadata)
    if not (source.get("version")==SOURCE_VERSION and source.get("symbol")==SOURCE_SYMBOL
            and source.get("non_adjusted") is True
            and source.get("authorization")=="RESEARCH_ONLY_NO_TRADING"
            and source.get("bar_time_basis")=="Asia/Shanghai 15m bar-end"):
        raise ValueError("15m research provenance not authorized")
    files=source.get("files_sha256")
    if not isinstance(files,dict) or not files or len(files)!=source.get("partitions"):
        raise ValueError("Incomplete 15m partition manifest")
    frames=[]
    for relative,sha in sorted(files.items()):
        path=Path(relative)
        if (path.is_absolute() or ".." in path.parts or
                not relative.startswith("kline_15m/year=") or not relative.endswith("/data.parquet")):
            raise ValueError("Bad 15m source path")
        filename=root/path
        if hashlib.sha256(filename.read_bytes()).hexdigest()!=sha:
            raise ValueError("15m partition SHA mismatch: "+relative)
        frames.append(pd.read_parquet(filename))
    minutes=pd.concat(frames,ignore_index=True)
    if not REQUIRED.issubset(minutes) or not minutes.ts_code.eq(SOURCE_SYMBOL).all():
        raise ValueError("Wrong 15m schema or symbol")
    if len(minutes)!=int(source["rows"]):
        raise ValueError("Wrong 15m source row count")
    minutes["trade_time"]=pd.to_datetime(minutes.trade_time,errors="raise")
    if minutes.trade_time.duplicated().any():
        raise ValueError("Duplicate cross-partition 15m timestamps")
    minutes["day"]=minutes.trade_time.dt.normalize()
    selected=daily.loc[daily.vendor_symbol.eq(SOURCE_SYMBOL)].copy()
    selected["trade_date"]=pd.to_datetime(selected.trade_date).dt.normalize()
    if selected.trade_date.duplicated().any():
        raise ValueError("Duplicate independent Gold symbol-day")
    closes=selected.set_index("trade_date")["close"]
    valid={}
    rejected={}
    for day,group in minutes.groupby("day",sort=True):
        key=str(day.date())
        if day not in closes.index:
            rejected[key]="NO_INDEPENDENT_DAILY_GOLD_CLOSE"
            continue
        try:
            valid[key]=validate_one_day(group.sort_values("trade_time"),float(closes.loc[day]))
        except (ValueError,TypeError) as exc:
            rejected[key]=str(exc)
    candidate_dates=sorted(str(day.date()) for day,rows in signals.items()
        if any(symbol==SOURCE_SYMBOL for symbol,_ in rows)
        and (start is None or day >= pd.Timestamp(start)))
    matched={day:valid[day] for day in candidate_dates if day in valid}
    trade_rows=[{"signal_date":t["signal_date"],"pnl_cny":t["pnl_cny"],
                 "minute":valid.get(t["signal_date"])}
                for t in trades if t["symbol"]==SOURCE_SYMBOL]
    return {"mode":"SHADOW_ONLY_NO_ORDER_CHANGES", "source":"Baostock", "scope":[SOURCE_SYMBOL],
            "audit_start_date": start,
            "source_manifest_sha256":hashlib.sha256(metadata).hexdigest(),
            "source_capture_at":source.get("captured_at_utc"),
            "minute_distinct_days":int(minutes.day.nunique()),
            "valid_daily_crosschecked_days":len(valid), "rejected_days":len(rejected),
            "rejected_examples":dict(list(rejected.items())[:10]),
            "stock_daily_signal_days":len(candidate_dates),
            "verified_minute_signal_days":len(matched),
            "missing_minute_signal_dates":[d for d in candidate_dates if d not in valid][:100],
            "last_hour_ge30pct_signal_days":sum(v["last_hour_ge30_shadow"] for v in matched.values()),
            "matched_paper_trades":trade_rows,
            "marketwide_minute_coverage_verified":False,
            "incremental_15m_profitability_proven":False}
