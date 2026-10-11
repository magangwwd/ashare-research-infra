"""Inspect point-in-time risk data without assuming a schema or filling absent dates."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import pandas as pd

def summarize(path: Path) -> dict:
    files = sorted(path.rglob("*.parquet"))
    if not files: return {"status":"MISSING","files":0,"rows":0}
    seen=[]; cols=None; first=[]; n=0; nn={}
    for f in files:
        df=pd.read_parquet(f)
        if cols is None:
            cols = [{"name":c,"dtype":str(df[c].dtype)} for c in df.columns]
            first = df.head(2).astype(str).to_dict("records")
        n+=len(df)
        if "trade_date" in df:
            dt=pd.to_datetime(df["trade_date"],errors="coerce")
            seen.extend(dt.dropna().dt.strftime("%Y-%m-%d").unique().tolist())
        for k in ("instrument_id","vendor_symbol","ts_code","trade_date","up_limit","down_limit","limit_up","limit_down","name","change_reason","start_date","end_date","suspend_type"):
            if k in df: nn[k]=nn.get(k,0)+int(df[k].notna().sum())
    unique=sorted(set(seen))
    return {"status":"PRESENT","files":len(files),"rows":n,"columns":cols,"sample":first,
            "min_trade_date":unique[0] if unique else None,"max_trade_date":unique[-1] if unique else None,
            "distinct_trade_dates":len(unique),"nonnul_counts":nn}
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--root",type=Path,required=True)
    ap.add_argument("--out",type=Path,required=True)
    a=ap.parse_args()
    datasets=("daily_limits","suspension_events","name_history")
    report={d:summarize(a.root/d) for d in datasets}
    # Contract: missing data must be UNVERIFIED rather than implicitly accepted.
    report["rule"]="A missing PIT row is UNKNOWN, not a safe zero-risk day."
    report["safe_to_join_entire_2024_2026"]=False
    a.out.parent.mkdir(parents=True,exist_ok=True)
    a.out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print("GOLD_RISK_SCHEMA_AUDIT="+json.dumps(report,ensure_ascii=False),flush=True)
if __name__=="__main__":main()
