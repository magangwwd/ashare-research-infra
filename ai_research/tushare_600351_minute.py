"""Bounded Tushare historical 15-minute capture for ONE stock 600351.SH.

This dataset is isolated from the full-market Task01 and frozen R1.
A single live permission probe must yield actual stock bars before backfill;
an empty/denied response NEVER becomes a successful backfill.
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

SYMBOL = "600351.SH"
START = date(2023, 10, 16)
END = date(2026, 10, 16)
SCHEMA = "TUSHARE_600351_UNADJUSTED_15M_V1"
LIMIT = 7900
FIELDS = ("ts_code", "trade_time", "open", "high", "low", "close", "vol", "amount")


class SourceUnavailable(RuntimeError):
    pass


def monthly_windows(start=START, end=END):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        last = date(y, m, calendar.monthrange(y, m)[1])
        yield max(start, date(y, m, 1)), min(end, last)
        if m == 12:
            y, m = y+1, 1
        else:
            m += 1


def bound_request(first: date, last: date):
    assert START <= first <= last <= END
    return {
        "ts_code": SYMBOL,
        "freq": "15min",
        "start_date": first.isoformat()+" 09:00:00",
        "end_date": last.isoformat()+" 15:31:00",
    }


def normalize_response(response, first, last):
    if response is None or not isinstance(response, pd.DataFrame):
        raise SourceUnavailable("Tushare minute response is not a dataframe")
    if response.empty:
        raise SourceUnavailable(
            f"Historical minute source returned ZERO rows for {first}..{last}; "
            "permission/coverage not verified")
    if len(response) >= LIMIT:
        raise SourceUnavailable("Source returned near 8000-row ceiling: split request")
    required = set(FIELDS) - set(response.columns)
    if required:
        raise SourceUnavailable("Minutes schema missing "+",".join(sorted(required)))
    frame = response.loc[:,list(FIELDS)].copy()
    if frame.ts_code.ne(SYMBOL).any():
        raise SourceUnavailable("Tushare returned foreign ticker rows")
    ts = pd.to_datetime(frame.trade_time, errors="coerce")
    if ts.isna().any() or ts.dt.tz is not None:
        raise SourceUnavailable("Bad or timezone-ambiguous minute time values")
    if (ts.dt.date < first).any() or (ts.dt.date > last).any():
        raise SourceUnavailable("Minute source leaked bars outside requested dates")
    hhmm=ts.dt.strftime("%H:%M")
    expected={"09:30", "09:45", "10:00", "10:15", "10:30", "10:45",
              "11:00", "11:15", "11:30", "13:15", "13:30", "13:45",
              "14:00", "14:15", "14:30", "14:45", "15:00"}
    if not hhmm.isin(expected).all():
        raise SourceUnavailable("Minute-bar timestamps violate known 15-minute bar-end contract")
    if frame.trade_time.duplicated().any():
        raise SourceUnavailable("Duplicated minute primary keys")
    for col in ("open", "high", "low", "close", "vol", "amount"):
        frame[col]=pd.to_numeric(frame[col],errors="coerce")
    if (frame[["open", "high", "low", "close", "vol", "amount"]]
            .isna().any().any()):
        raise SourceUnavailable("Null/non-numeric minute price or amount")
    if (frame[["open", "high", "low", "close"]] <= 0).any().any():
        raise SourceUnavailable("Zero or negative minute price")
    if ((frame["high"] < frame[["open","close","low"]].max(axis=1)) |
        (frame["low"] > frame[["open","close","high"]].min(axis=1))).any():
        raise SourceUnavailable("Inconsistent OHLC minute bars")
    if (frame[["vol","amount"]] < 0).any().any():
        raise SourceUnavailable("Negative volume or traded value")
    frame["trade_time"]=ts
    return frame.sort_values("trade_time").reset_index(drop=True)


def query_live(api, first, last, *, sleeper=time.sleep, retry_count=2):
    args=bound_request(first,last)
    token=os.environ.get("TUSHARE_TOKEN","").strip()
    for attempt in range(retry_count):
        try:
            # repo data_pipeline TushareClient uses the dedicated stk_mins route
            # explicitly; avoid assuming a successful daily token can read it.
            result=api.query("stk_mins",**args)
            return normalize_response(result,first,last)
        except SourceUnavailable:
            raise
        except Exception as exc:
            sanitized=str(exc).replace(token,"[REDACTED]") if token else str(exc)
            if attempt+1==retry_count:
                raise SourceUnavailable(
                    f"stk_mins request failed ({first}..{last}): "
                    + sanitized[:350]) from None
            sleeper(2**attempt)
    raise AssertionError("unreachable")


def build(api, out: Path, *, sleeper=time.sleep, start=START, end=END):
    windows=list(monthly_windows(start,end))
    # Current recent sample: prove the real endpoint responds BEFORE bulk requests.
    probe_first,probe_last=windows[-1]
    probe=query_live(api, probe_first, probe_last, sleeper=sleeper)
    if probe.empty:
        raise SourceUnavailable("Probe empty: never proceed with backfill")
    out.mkdir(parents=True,exist_ok=True)
    log=[]
    for first,last in windows:
        if (first,last)==(probe_first,probe_last):
            bars=probe
        else:
            # Gentle rate limit, separate from retry backoff.
            sleeper(0.75)
            bars=query_live(api,first,last,sleeper=sleeper)
        path=out / f"year={first.year}" / f"month={first.month:02d}" / "data.parquet"
        path.parent.mkdir(parents=True,exist_ok=True)
        bars.to_parquet(path,index=False,compression="zstd")
        log.append({
            "start":str(first),"end":str(last),
            "bars":len(bars),"raw_trading_days":int(bars.trade_time.dt.date.nunique()),
            "first_bar":bars.trade_time.min().isoformat(),
            "last_bar":bars.trade_time.max().isoformat(),
            "sha256":hashlib.sha256(path.read_bytes()).hexdigest(),
            "path":str(path.relative_to(out))
        })
    summary={
        "schema":SCHEMA,"symbol":SYMBOL,"start":str(start),"as_of":str(end),
        "source":"TUSHARE stk_mins historical (separate permission)",
        "unadjusted":True,"bar_time":"Asia/Shanghai exchange session bar END",
        "output_partitions":len(log),"total_raw_bars":sum(x["bars"] for x in log),
        "source_permission_verified_with_nonempty_probe":True,
        "probe_bar_count":len(probe),"months":log,
        "finished_utc":datetime.now(timezone.utc).isoformat(),
        "research_only":True,"real_orders":False}
    (out/"source_manifest.json").write_text(json.dumps(
        summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return summary


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output-dir",type=Path,required=True)
    args=parser.parse_args()
    token=os.environ.get("TUSHARE_TOKEN","").strip()
    if not token:
        raise SystemExit("TUSHARE_TOKEN secret missing; live source NOT tested")
    import tushare as ts
    ts.set_token(token)
    api=ts.pro_api(token)
    try:
        summary=build(api,args.output_dir)
    except SourceUnavailable as ex:
        raise SystemExit(f"SOURCE_UNAVAILABLE: {ex}") from None
    print(json.dumps({"status":"LIVE_MINUTE_BACKFILL_NONEMPTY_NOT_YET_DAILY_VALIDATED",
                      "months":summary["output_partitions"],
                      "raw_bars":summary["total_raw_bars"]}))


if __name__=="__main__":
    main()
