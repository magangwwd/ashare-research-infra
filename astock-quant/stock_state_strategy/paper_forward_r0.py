"""Append-only R0 daily signal snapshot after new Gold closes, PAPER ONLY.

Never backfill a snapshot dated before the day it was actually first observed.
This module does not place orders, compute guaranteed stops, or treat a signal
as a safe buy. Trigger only after dated Gold price/limit data is verified.
"""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
from datetime import datetime, timezone
from state_run_contracts import require_current_closed_session
import pandas as pd
import numpy as np
import research_mainboard_daily_pit_v0 as baseline
from gold_pit_risk_guards import GoldPITRisk

FIRST_UNSEEN=pd.Timestamp("2026-10-12")

def verified_as_of(manifest:dict, requested:pd.Timestamp)->None:
    if requested<FIRST_UNSEEN:
        raise RuntimeError("Refuse retroactive forward tracking before 2026-10-12")
    observed=pd.to_datetime(manifest.get("as_of_date"),errors="coerce")
    if pd.isna(observed) or observed.normalize()!=requested.normalize():
        raise RuntimeError(f"No exact dated validated Gold snapshot: expected {requested.date()}, manifest {observed}")
    if manifest.get("validation",{}).get("primary_keys_unique") is not True:
        raise RuntimeError("Gold canonical snapshot is not marked primary-key valid")

def recent_panel(root:Path, cutoff:pd.Timestamp)->pd.DataFrame:
    desired={
      "daily_prices":["vendor_symbol","trade_date","open","high","low","close",
                      "volume_shares","turnover_cny","is_quality_valid"],
      "adjustment_factors":["vendor_symbol","trade_date","adjustment_factor"],
    }
    data={}
    for ds,cols in desired.items():
        frames=[]
        for year in (2025,2026):
            for file in sorted((root/ds/f"year={year}").glob("month=*/data.parquet")):
                frames.append(pd.read_parquet(file,columns=cols))
        if not frames:raise RuntimeError("No fresh Gold "+ds)
        data[ds]=pd.concat(frames,ignore_index=True)
    panel=data["daily_prices"].merge(data["adjustment_factors"],
        on=["vendor_symbol","trade_date"],how="left",validate="one_to_one")
    panel["trade_date"]=pd.to_datetime(panel["trade_date"])
    panel=panel.loc[panel["trade_date"]<=cutoff].copy()
    if panel.empty or panel["trade_date"].max()!=cutoff:
        raise RuntimeError("No validated trade-date price rows at desired day")
    if panel["adjustment_factor"].isna().any():
        raise RuntimeError("Cannot compute technical features with missing factors")
    good=(panel["is_quality_valid"].eq(True)
          & panel[["open","high","low","close","adjustment_factor"]].gt(0).all(axis=1)
          & panel["volume_shares"].gt(0))
    panel=panel.loc[good].copy()
    if not (panel["trade_date"]==cutoff).any():
        raise RuntimeError("All source-day records were invalid")
    for c in ("open","high","low","close"):
        panel["p_"+c]=panel[c]*panel["adjustment_factor"]
    return panel.sort_values(["vendor_symbol","trade_date"],kind="stable")

def snapshot(panel:pd.DataFrame,guard:GoldPITRisk,cutoff:pd.Timestamp)->list[dict]:
    entries=[]
    total=0
    for sym,series in panel.groupby("vendor_symbol",sort=True):
        if series["trade_date"].iloc[-1]!=cutoff:continue
        total+=1
        features=baseline.history_features(series)
        r=features.iloc[-1]
        if not r["signal"]:continue
        raw={c:r[c] for c in ("open","high","low","close")}
        # A risk status is required on the SIGNAL day, and must be rechecked at
        # the unknown following OPEN before any theoretical execution.
        block=guard.entry_reason(str(sym),cutoff,raw)
        if block is not None:continue
        if not np.isfinite(r["atr14"]) or r["atr14"]<=0:continue
        planned_risk=float(2*r["atr14"]/(r["p_close"]*(1+baseline.SLIP)))
        if not (0.015<=planned_risk<=0.04):continue
        entries.append({
          "symbol":str(sym),"signal_date":str(cutoff.date()),
          "pattern":str(r["signal"]),"signal_strength":round(float(r["signal_strength"]),4),
          "raw_close":round(float(r["close"]),3),
          "preliminary_max_next_open_raw":round(float(r["close"])*1.01,3),
          "preliminary_risk_frac_at_signal_close":round(planned_risk,6),
          "next_session": "UNKNOWN_UNTIL_SOURCE_CONFIRMATION",
          "entry_decision":"NOT_AUTHORIZED_PENDING_NEXT_OPEN_LIMIT_ST_AND_ATR_RECHECK",
        })
    entries.sort(key=lambda r:(-r["signal_strength"],r["symbol"]))
    return entries

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--manifest",required=True,type=Path)
    p.add_argument("--market-root",required=True,type=Path)
    p.add_argument("--risk-root",required=True,type=Path)
    p.add_argument("--as-of",required=True)
    p.add_argument("--output",required=True,type=Path)
    args=p.parse_args()
    day=pd.Timestamp(args.as_of).normalize()
    meta=json.loads(args.manifest.read_text(encoding="utf-8"))
    verified_as_of(meta,day)
    calendar=pd.read_parquet(args.market_root / "trading_calendar.parquet")
    require_current_closed_session(str(day.date()),
        list(zip(calendar["calendar_date"], calendar["is_open"].fillna(False))),
        datetime.now(timezone.utc))
    panel=recent_panel(args.market_root,day)
    risks=GoldPITRisk(args.risk_root)
    if day not in {d for _,d in risks.limits}:
        raise RuntimeError("No PIT limit-price coverage for current forward date")
    entries=snapshot(panel,risks,day)
    manifest_bytes=args.manifest.read_bytes()
    report={
      "date":str(day.date()),
      "state":"CLOSE_OF_DAY_RESEARCH_ONLY_NOT_A_BUY_RECOMMENDATION",
      "source_manifest_sha256":hashlib.sha256(manifest_bytes).hexdigest(),
      "source_manifest_as_of":meta["as_of_date"],
      "source_rows_today":int(panel["trade_date"].eq(day).sum()),
      "candidate_count":len(entries),
      "rule":"R0: 2ATR14/estimated entry<=4%, preliminary next open <= +1%; max 0.25% account risk, max 10% per stock",
      "risk_context":risks.summary(),
      "no_lookahead":"Signals use only dated Gold prices through signal close. Next open is NOT known.",
      "candidates":entries,
      "warnings":[
        "No limit/ST/news validation for the next actual session yet; candidates require recheck.",
        "A stale Gold file must never masquerade as a live quote.",
        "Price threshold is PRELIMINARY raw T-close x1.01; corporate actions can rebase it.",
        "PIT risk status unknown means no trade, not zero risk.",
        "The R0 rule lost money in the 2025-2026 retrospective, and is still unvalidated forward.",
      ]
    }
    args.output.mkdir(parents=True,exist_ok=True)
    dest=args.output/f"signal_snapshot_{day.date()}.json"
    if dest.exists():raise RuntimeError("Local forward snapshot exists: immutability")
    dest.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding="utf-8")
    print("FORWARD_R0_STATUS="+json.dumps({
        "date":report["date"],"source_rows_today":report["source_rows_today"],
        "candidate_count":report["candidate_count"],"state":report["state"]},
        ensure_ascii=False),flush=True)
if __name__=="__main__":main()
