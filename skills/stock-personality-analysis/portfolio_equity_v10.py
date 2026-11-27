#!/usr/bin/env python3
"""v1.0 retrospective 40k-CNY one-position daily equity audit; NOT a forward test."""
import argparse,csv,hashlib,json
from collections import defaultdict
from pathlib import Path

def prices(paths,ticker="600031.SH"):
    rows={}
    for path in paths:
        with open(path,encoding="utf-8-sig",newline="") as f:
            for r in csv.DictReader(f):
                if r["ts_code"]!=ticker:continue
                d=r["trade_date"]
                if d in rows:raise ValueError("duplicate date")
                rows[d]={k:float(r[k]) for k in ("open","high","low","close")}
                p=rows[d]
                if not (p["low"]<=min(p["open"],p["close"])<=max(p["open"],p["close"])<=p["high"]):
                    raise ValueError("bad OHLC")
    return dict(sorted(rows.items()))

def simulate(trades,ohlc,initial=40000.,minimum_fee=0.):
    if initial<=0 or minimum_fee<0:raise ValueError("invalid cash/fee")
    entries=defaultdict(list)
    for t in trades:
        if not t["signal_date"]<t["entry_date"]<t["exit_date"]:raise ValueError("chronology")
        for d,key in ((t["entry_date"],"entry_open"),(t["exit_date"],"exit_open")):
            if d not in ohlc or abs(ohlc[d]["open"]-t[key])>1e-6:raise ValueError("price mismatch")
        entries[t["entry_date"]].append(t)
    if any(len(v)>1 for v in entries.values()):raise ValueError("multiple entries")
    fee=lambda v:max(v*.0003,minimum_fee)
    cash=float(initial);position=None;curve=[];completed=[];rejected=[]
    for d,p in ohlc.items():
        if position and position["exit_date"]==d:
            gross=position["shares"]*p["open"]*(1-.0005)
            proceeds=gross-fee(gross)-gross*.0005
            cash+=proceeds
            completed.append({**position,"realized_net_pnl":round(proceeds-position["spent"],2)})
            position=None
        for t in entries[d]:
            if position:raise ValueError("overlapping schedule")
            unit=p["open"]*(1+.0005)
            shares=int(cash//(100*unit))*100
            while shares and shares*unit+fee(shares*unit)>cash+1e-8:shares-=100
            if shares==0:
                rejected.append(d);continue
            spent=shares*unit+fee(shares*unit)
            cash-=spent
            position={"entry_date":d,"exit_date":t["exit_date"],"signal":t["signal"],
                      "shares":shares,"spent":spent}
        if cash< -1e-7:raise ValueError("negative cash")
        curve.append({"date":d,"cash":round(cash,2),"shares":position["shares"] if position else 0,
                      "close":p["close"],"equity":round(cash+(position["shares"]*p["close"] if position else 0),2)})
    if position or not curve:raise ValueError("unclosed or empty")
    peak=initial;dd=0.;year_ends={}
    for r in curve:
        peak=max(peak,r["equity"])
        r["drawdown_pct"]=round(100*(r["equity"]/peak-1),5)
        dd=min(dd,r["drawdown_pct"])
        year_ends[r["date"][:4]]=r["equity"]
    previous=initial;annual={}
    for year,end in year_ends.items():
        annual[year]=round(100*(end/previous-1),4);previous=end
    return {"starting_cash_yuan":initial,"ending_equity_yuan":curve[-1]["equity"],
            "total_return_pct":round(100*(curve[-1]["equity"]/initial-1),4),
            "net_profit_yuan":round(curve[-1]["equity"]-initial,2),
            "max_drawdown_pct":round(dd,4),"trades_executed":len(completed),
            "trades_rejected_cash":len(rejected),"days_in_position":sum(r["shares"]>0 for r in curve),
            "days_observed":len(curve),"annual_returns_pct":annual,
            "minimum_fee_yuan":minimum_fee,"executed_trades":completed,
            "daily_equity":curve,"rejected_entries":rejected}

def main():
    a=argparse.ArgumentParser()
    a.add_argument("--audit",required=True);a.add_argument("--daily",nargs="+",required=True)
    a.add_argument("--out",required=True);args=a.parse_args()
    audit=json.loads(Path(args.audit).read_text(encoding="utf-8"))
    if audit["priority"]!=["D_breakout","G_upper_wick","B_breakdown","F_tight_lowvol"]:
        raise ValueError("priority changed")
    data=prices(args.daily)
    if len(data)!=audit["input_sessions"] or len(audit["selected_trades"])!=60:
        raise ValueError("coverage or frozen schedule changed")
    sha=lambda f:hashlib.sha256(Path(f).read_bytes()).hexdigest()
    result={"scope":"RETROSPECTIVE UNADJUSTED OHLC; NOT BLIND HOLDOUT OR FILL-VERIFIED",
            "source_sha256":{Path(f).name:sha(f) for f in [args.audit,*args.daily]},
            "base":simulate(audit["selected_trades"],data),
            "minimum_5_yuan":simulate(audit["selected_trades"],data,minimum_fee=5.),
            "limitations":["No independent dividend/corporate-action cashflow ledger",
                           "No price-limit, suspension, opening-auction or broker fill verification",
                           "Historically selected rules, not prospective holdout"]}
    Path(args.out).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print({k:result["base"][k] for k in ("ending_equity_yuan","total_return_pct","max_drawdown_pct","trades_executed")})

if __name__=="__main__":main()
