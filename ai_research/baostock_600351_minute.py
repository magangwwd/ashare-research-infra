"""Single-stock exploratory free Baostock 15-minute historical source.

Does not change the canonical Tushare Gold history or frozen R1.
Non-adjusted data is collected with explicit source dates; no orders or API keys.
Any missing year aborts rather than silently pretending historical completeness.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

VERSION = "BAOSTOCK_600351_15M_PROBE_V1"
QUERY_WINDOWS = [
    ("2023-10-01", "2023-12-31"),
    ("2024-01-01", "2024-12-31"),
    ("2025-01-01", "2025-12-31"),
    ("2026-01-01", "2026-10-16"),
]
FIELDS = "date,time,code,open,high,low,close,volume,amount,adjustflag"
TARGET_SOURCE = "sh.600351"
TARGET_DEST = "600351.SH"


def normalize_row_batch(frame):
    missing = set(FIELDS.split(",")) - set(frame.columns)
    if missing:
        raise ValueError("Baostock 15m fields absent: "+",".join(sorted(missing)))
    if frame.empty:
        raise ValueError("Baostock returns empty history: not proof of 0 events")
    if not frame["code"].eq(TARGET_SOURCE).all():
        raise ValueError("Baostock response includes unexpected stock")
    if not frame["adjustflag"].eq("3").all():
        raise ValueError("Non-adjusted historical price contract broken")
    if not frame["time"].astype(str).str.fullmatch(r"20\d{15}").all():
        raise ValueError("Unexpected Baostock bar end timestamp")
    d = pd.to_datetime(frame["time"].str[:12], format="%Y%m%d%H%M",
                       errors="raise")
    actual_day = pd.to_datetime(frame["date"], errors="raise")
    if not (d.dt.date == actual_day.dt.date).all():
        raise ValueError("Date/bar timestamp inconsistency")
    x = pd.DataFrame({
        "ts_code": TARGET_DEST,
        "trade_time": d,
        "open": pd.to_numeric(frame["open"], errors="raise"),
        "high": pd.to_numeric(frame["high"], errors="raise"),
        "low": pd.to_numeric(frame["low"], errors="raise"),
        "close": pd.to_numeric(frame["close"], errors="raise"),
        "vol": pd.to_numeric(frame["volume"], errors="raise"),
        "amount": pd.to_numeric(frame["amount"], errors="coerce"),
    })
    if ((x[["open","high","low","close"]]<=0).any().any()
            or (x["vol"]<0).any()
            or (x["high"]<x[["open","close","low"]].max(axis=1)).any()
            or (x["low"]>x[["open","close","high"]].min(axis=1)).any()):
        raise ValueError("Invalid vendor 15m OHLCV")
    if x.trade_time.duplicated().any():
        raise ValueError("Duplicate vendor bar time")
    return x.sort_values("trade_time").reset_index(drop=True)


def fetch():
    import baostock as bs
    login = bs.login()
    if str(login.error_code) != "0":
        raise RuntimeError("Free provider login failed: "+str(login.error_msg)[:200])
    all_data = []
    audit = []
    try:
        for start,end in QUERY_WINDOWS:
            req = bs.query_history_k_data_plus(
                TARGET_SOURCE, FIELDS, start_date=start, end_date=end,
                frequency="15", adjustflag="3")
            if str(req.error_code) != "0":
                raise RuntimeError(f"Baostock query failed {start}..{end}: "
                                   +str(req.error_msg)[:160])
            rows = []
            while req.next():
                rows.append(req.get_row_data())
                if len(rows) > 12000:
                    raise RuntimeError("Unexpected response size, avoid truncation")
            if not rows:
                raise RuntimeError(f"No history for {start}..{end}; fail closed")
            raw = pd.DataFrame(rows, columns=req.fields)
            normalized = normalize_row_batch(raw)
            all_data.append(normalized)
            audit.append({"start":start,"end":end,"rows":len(raw),
                          "first_bar":str(normalized.trade_time.min()),
                          "last_bar":str(normalized.trade_time.max())})
    finally:
        bs.logout()
    result = pd.concat(all_data,ignore_index=True)
    if result.trade_time.duplicated().any():
        raise ValueError("Duplicate joined year partitions")
    return result, audit


def save(frame, audit, root):
    root.mkdir(parents=True,exist_ok=True)
    frame = frame.assign(year=frame.trade_time.dt.year,
                         month=frame.trade_time.dt.month)
    files = {}
    for (year, month), group in frame.groupby(["year","month"]):
        dest = root/f"kline_15m/year={year}/month={month:02d}/data.parquet"
        dest.parent.mkdir(parents=True,exist_ok=True)
        group.drop(columns=["year","month"]).to_parquet(dest,index=False,
                                                       compression="zstd")
        files[str(dest.relative_to(root))] = hashlib.sha256(dest.read_bytes()).hexdigest()
    manifest={
        "version":VERSION,"provider":"Baostock","symbol":TARGET_DEST,
        "non_adjusted":True,"bar_time_basis":"Asia/Shanghai 15m bar-end",
        "captured_at_utc":datetime.now(timezone.utc).isoformat(),
        "source_eligibility":"EXTERNAL_ALTERNATIVE_NOT_CANONICAL_TUSHARE",
        "request_windows":audit,"rows":int(len(frame)),
        "date_min":str(frame.trade_time.min()),"date_max":str(frame.trade_time.max()),
        "distinct_days":int(frame.trade_time.dt.date.nunique()),
        "partitions":len(files),"files_sha256":files,
        "authorization":"RESEARCH_ONLY_NO_TRADING",
    }
    (root/"provider_manifest.json").write_text(json.dumps(
        manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return manifest


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--output-dir",type=Path,required=True)
    args=ap.parse_args()
    frame,audit=fetch()
    m=save(frame,audit,args.output_dir)
    print(json.dumps({"status":"PROVIDER_DATA_SAVED_NOT_ACCEPTED_BY_DAILY_CROSSCHECK",
                      "rows":m["rows"],"days":m["distinct_days"],
                      "periods":m["partitions"]}))


if __name__=="__main__":
    main()
