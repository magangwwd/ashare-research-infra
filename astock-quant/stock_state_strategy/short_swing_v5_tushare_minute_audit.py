"""Tushare-only, pinned Ashare_Quant January-2026 minute-vs-V5 exit check.

All checks are *necessary*, not sufficient, for executable fills: minute bars
cannot reveal exact order queue, within-bar path or broker execution.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

import pandas as pd

PINNED_MANIFEST_SHA="b0b7b0222e6a2094a039ca3141bf027d0383320c28305141c495d7b45731cb4d"
PINNED_VERSION="2026-10-20T024536.666798_0000-39fecbf2"
BASE_DIR="data/clean/stk_mins/year=2026/month=01"
EXIT_INTRADAY={"STOP_INTRADAY_ASSUMED","TARGET_INTRADAY_ASSUMED"}
TIMES={"09:45","10:00","10:15","10:30","10:45","11:00","11:15","11:30",
       "13:15","13:30","13:45","14:00","14:15","14:30","14:45","15:00"}


def validate_file_manifest(production: dict, minute_root: Path):
    if production.get("version")!=PINNED_VERSION or not production.get("validated"):
        raise ValueError("Unknown or invalid published Tushare snapshot")
    files=[x for x in production["files"] if x["path"].startswith(BASE_DIR+"/")]
    if len(files)!=64: raise ValueError("Expected 64 Jan-2026 Tushare Clean minute partitions")
    matched=[]
    missing=[]
    for spec in files:
        rel=spec["path"].split(BASE_DIR+"/",1)[1]
        path=minute_root/rel
        if not path.exists():
            missing.append(rel)
            continue
        data=path.read_bytes()
        if len(data)!=spec["bytes"] or hashlib.sha256(data).hexdigest()!=spec["sha256"]:
            raise ValueError("Parquet partition does not match published SHA: "+rel)
        matched.append(path)
    return matched,missing


def inspect_v5_january(v5: dict, minute: pd.DataFrame, missing_partition_count: int)->dict:
    if v5.get("version")!="MAINBOARD_3TO5_CAUSAL_RANKING_PAPER_V5" or v5.get("period") != {
           "start":"2024-01-01","end":"2026-10-16"}:
        raise ValueError("Wrong historical V5 cohort")
    if [v["name"] for v in v5["variants"]] != [
        "V2_LOOSE_UNCHANGED","LESS_EXTENDED_BREAKOUT_FIRST","LESS_RECENT_RUNUP_FIRST"]:
        raise ValueError("Unexpected V5 variants")
    required={"ts_code","trade_time","trade_date","open","high","low","close"}
    if not required.issubset(minute.columns):
        raise ValueError("Missing minute columns")
    minute=minute.copy()
    minute["trade_time"]=pd.to_datetime(minute["trade_time"],errors="raise")
    minute["trade_date"]=pd.to_datetime(minute["trade_date"],errors="raise").dt.normalize()
    minute["trade_time_day"]=minute["trade_time"].dt.normalize()
    if not minute["trade_time_day"].eq(minute["trade_date"]).all():
        raise ValueError("Minute time disagrees with session day")
    if minute.duplicated(["ts_code","trade_time"]).any():raise ValueError("Duplicate minute key")
    if any(not pd.to_numeric(minute[c],errors="raise").gt(0).all()
           for c in ("open","high","low","close")):
        raise ValueError("Invalid positive OHLC")
    if any(not minute["low"].le(minute[c]).all() for c in ("open","close")) or any(
           not minute["high"].ge(minute[c]).all() for c in ("open","close")):
        raise ValueError("Malformed OHLC high or low")
    groups={(s,str(d.date())):x.sort_values("trade_time") for (s,d),x in
            minute.groupby(["ts_code","trade_date"])}
    details=[]
    for v in v5["variants"]:
        candidates=[t for t in v["trades"] if t["entry_date"].startswith("2026-01")
                    and t["exit_date"].startswith("2026-01")]
        for t in candidates:
            entry=groups.get((t["symbol"],t["entry_date"]))
            exit_=groups.get((t["symbol"],t["exit_date"]))
            def complete(df):
                return df is not None and len(df)==16 and {
                    d.strftime("%H:%M") for d in df["trade_time"]}==TIMES
            eok=complete(entry);xok=complete(exit_)
            row={"variant":v["name"],"symbol":t["symbol"],"entry_date":t["entry_date"],
                 "exit_date":t["exit_date"],"exit_reason":t["exit_reason"],
                 "entry_bars":0 if entry is None else len(entry),
                 "exit_bars":0 if exit_ is None else len(exit_),
                 "complete_entry_exit":bool(eok and xok),
                 "verified_fill":False, "bar_trigger_consistent":None}
            if eok and xok and not t.get("corporate_action_proxy"):
                raw_entry_px=float(t["entry_price_raw"])/(1+.001)
                row["entry_open_close_to_daily"]=bool(
                    abs(float(entry.iloc[0]["open"])-raw_entry_px)<=.02)
                stop=float(t["entry_price_raw"])*.97
                target=float(t["entry_price_raw"])*1.05
                if t["exit_reason"]=="STOP_INTRADAY_ASSUMED":
                    row["bar_trigger_consistent"]=bool(float(exit_["low"].min())<=stop+.02)
                elif t["exit_reason"]=="TARGET_INTRADAY_ASSUMED":
                    row["bar_trigger_consistent"]=bool(float(exit_["high"].max())>=target-.02)
            details.append(row)
    return {"version":"V5_TUSHARE_15M_JANUARY_2026_NECESSARY_ONLY",
            "minute_source":"Tushare clean stk_mins non-adjusted via Ashare_Quant",
            "published_version":PINNED_VERSION,
            "source_missing_partition_count":missing_partition_count,
            "january_completed_trade_variants":len(details),
            "both_entry_exit_dates_16_bars":sum(t["complete_entry_exit"] for t in details),
            "trigger_checked":sum(t["bar_trigger_consistent"] is not None for t in details),
            "trigger_inconsistent":sum(t["bar_trigger_consistent"] is False for t in details),
            "verified_actual_fills":0,"details":details,
            "limitations":["Full published snapshot may contain missing file IDs: absence is NOT evidence of complete bars",
               "Only January 2026 trades whose entry AND exit are in Jan are assessed",
               "V5 trades in 2024 and 2025 cannot be verified from published 2026-only minute files",
               "Even 16 valid minute bars cannot prove actual execution, order book, auction, or intrabar sequencing",
               "2026 was already a studied history; no independent OOS strategy profit validation"]}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--production-manifest",required=True,type=Path)
    p.add_argument("--minute-root",required=True,type=Path)
    p.add_argument("--v5-result",required=True,type=Path)
    p.add_argument("--output",required=True,type=Path)
    a=p.parse_args()
    raw=a.production_manifest.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=PINNED_MANIFEST_SHA:
        raise ValueError("Published manifest SHA mismatch")
    sources,missing=validate_file_manifest(json.loads(raw),a.minute_root)
    if not sources:raise ValueError("No verified Tushare minute parquet found")
    frames=[pd.read_parquet(p) for p in sources]
    data=pd.concat(frames,ignore_index=True)
    out=inspect_v5_january(json.loads(a.v5_result.read_text()),data,len(missing))
    out["verified_minute_partition_count"]=len(sources)
    if a.output.exists():raise FileExistsError("Historical evidence is immutable")
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    print("V5_TUSHARE_JAN_15M_RESEARCH_ONLY",json.dumps({
        "partitions":len(sources),"missing_partitions":len(missing),
        "trades":out["january_completed_trade_variants"],
        "both_days_16_bars":out["both_entry_exit_dates_16_bars"],
        "checked_triggers":out["trigger_checked"],
        "inconsistent_triggers":out["trigger_inconsistent"],
        "actual_fills_verified":0},ensure_ascii=False))


if __name__=="__main__":main()
