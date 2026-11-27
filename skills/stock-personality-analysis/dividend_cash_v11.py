#!/usr/bin/env python3
"""v1.1 retrospective dividend cash ledger; frozen trades, NOT verified fills."""
import argparse,csv,json,hashlib
from pathlib import Path
from collections import defaultdict
from portfolio_equity_v10 import simulate as baseline_simulate, prices as read_prices

def load_daily(paths,ticker="600031.SH"):
    rows={}
    for path in paths:
        with open(path,encoding="utf-8-sig",newline="") as f:
            for r in csv.DictReader(f):
                if r["ts_code"]!=ticker:continue
                d=r["trade_date"]
                if d in rows:raise ValueError("duplicate daily key")
                rows[d]={k:float(r[k]) for k in ("open","high","low","close")}
                rows[d]["pre_close"]=float(r["pre_close"]) if r.get("pre_close") else None
    return dict(sorted(rows.items()))

def validate_events(daily,events,tolerance=.011):
    dates=list(daily)
    if len(set(e["ex_date"] for e in events))!=len(events):
        raise ValueError("duplicate ex-date")
    for e in events:
        d=e["ex_date"];rec=e["record_date"];amount=float(e["cash_per_share_yuan"])
        if d not in daily or rec not in daily or amount<=0:raise ValueError("bad event")
        i=dates.index(d)
        if i==0 or dates[i-1]!=rec:raise ValueError("bad record date")
        pre=daily[d]["pre_close"]
        if pre is None or abs(daily[rec]["close"]-pre-amount)>tolerance:
            raise ValueError("dividend/pre_close mismatch")
    return events

def simulate(trades,daily,events,initial=40000.,minimum_fee=5.,tax_rate=0.):
    if initial<=0 or minimum_fee<0 or not 0<=tax_rate<=1:raise ValueError("invalid parameters")
    entries=defaultdict(list)
    for t in trades:
        if not t["signal_date"]<t["entry_date"]<t["exit_date"]:raise ValueError("bad chronology")
        for date,field in ((t["entry_date"],"entry_open"),(t["exit_date"],"exit_open")):
            if date not in daily or abs(daily[date]["open"]-float(t[field]))>1e-6:
                raise ValueError("bad trade open")
        entries[t["entry_date"]].append(t)
    if any(len(v)>1 for v in entries.values()):raise ValueError("multiple entries")
    by_ex={e["ex_date"]:e for e in events}
    fee=lambda v:max(v*.0003,minimum_fee)
    cash=float(initial);holding=None;curve=[];completed=[];dividends=[];fills=[];rejected=[]
    for day,p in daily.items():
        # Entitlement is determined BEFORE any ex-date opening-auction trades.
        entitled=holding["shares"] if holding else 0
        if holding and holding["exit_date"]==day:
            gross=holding["shares"]*p["open"]*(1-.0005)
            proceeds=gross-fee(gross)-gross*.0005
            cash+=proceeds
            completed.append({**holding,"realized_net_pnl_ex_dividend":round(proceeds-holding["spent"],2)})
            fills.append({"date":day,"side":"sell","open":p["open"],"high":p["high"],"low":p["low"],"pre_close":p["pre_close"]})
            holding=None
        for t in entries.get(day,[]):
            if holding:raise ValueError("overlap")
            unit=p["open"]*(1+.0005)
            shares=int(cash//(100*unit))*100
            while shares and shares*unit+fee(shares*unit)>cash+1e-8:shares-=100
            if not shares:
                rejected.append(day);continue
            spent=shares*unit+fee(shares*unit)
            cash-=spent
            holding={"signal":t["signal"],"entry_date":day,"exit_date":t["exit_date"],
                     "shares":shares,"spent":spent}
            fills.append({"date":day,"side":"buy","open":p["open"],"high":p["high"],"low":p["low"],"pre_close":p["pre_close"]})
        # Conservative settlement: credit at end of ex-date, never at opening.
        if day in by_ex:
            e=by_ex[day]
            gross=round(entitled*float(e["cash_per_share_yuan"]),2)
            tax=round(gross*tax_rate,2)
            cash+=gross-tax
            dividends.append({"record_date":e["record_date"],"ex_date":day,
                              "shares":entitled,"per_share_yuan":float(e["cash_per_share_yuan"]),
                              "gross_yuan":gross,"assumed_tax_yuan":tax})
        if cash< -1e-7:raise ValueError("negative cash")
        curve.append({"date":day,"cash":round(cash,2),"shares":holding["shares"] if holding else 0,
                      "close":p["close"],"equity":round(cash+(holding["shares"]*p["close"] if holding else 0),2)})
    if holding or not curve:raise ValueError("unclosed or empty")
    peak=initial;worst=0.;annual_end={}
    for r in curve:
        peak=max(peak,r["equity"]);r["drawdown_pct"]=round(100*(r["equity"]/peak-1),5)
        worst=min(worst,r["drawdown_pct"]);annual_end[r["date"][:4]]=r["equity"]
    annual={};prev=initial
    for year,end in annual_end.items():
        annual[year]=round(100*(end/prev-1),4);prev=end
    for f in fills:
        pre=f["pre_close"];f["missing_pre_close"]=pre is None
        f["one_price_day"]=abs(f["high"]-f["low"])<1e-8
        f["near_10pct_limit"]=bool(pre and (f["open"]/pre>=1.098 or f["open"]/pre<=.902))
        f["possible_unfillable_limit"]=f["one_price_day"] and bool(pre and (
            (f["side"]=="buy" and f["open"]/pre>=1.098) or
            (f["side"]=="sell" and f["open"]/pre<=.902)))
    return {"ending_equity_yuan":curve[-1]["equity"],
            "total_return_pct":round(100*(curve[-1]["equity"]/initial-1),4),
            "max_drawdown_pct":round(worst,4),"trades_executed":len(completed),
            "days_observed":len(curve),"annual_returns_pct":annual,
            "dividend_gross_yuan":round(sum(d["gross_yuan"] for d in dividends),2),
            "dividend_ledger":dividends,"fills":fills,
            "fill_count":len(fills),"fill_missing_pre_close":sum(f["missing_pre_close"] for f in fills),
            "fill_near_10pct_limit":sum(f["near_10pct_limit"] for f in fills),
            "fill_possible_unfillable_limit":sum(f["possible_unfillable_limit"] for f in fills),
            "daily_equity":curve,"cash_rejections":rejected}

def main():
    a=argparse.ArgumentParser()
    a.add_argument("--audit",required=True);a.add_argument("--daily",required=True,nargs="+")
    a.add_argument("--events",required=True);a.add_argument("--out",required=True)
    args=a.parse_args()
    audit=json.loads(Path(args.audit).read_text())
    if audit["priority"]!=["D_breakout","G_upper_wick","B_breakdown","F_tight_lowvol"] or len(audit["selected_trades"])!=60:
        raise ValueError("frozen schedule changed")
    daily=load_daily(args.daily)
    if len(daily)!=audit["input_sessions"]:raise ValueError("coverage changed")
    events=validate_events(daily,json.loads(Path(args.events).read_text()))
    base=baseline_simulate(audit["selected_trades"],read_prices(args.daily),minimum_fee=5.)
    gross=simulate(audit["selected_trades"],daily,events)
    tax20=simulate(audit["selected_trades"],daily,events,tax_rate=.2)
    assert simulate(audit["selected_trades"],daily,[])["ending_equity_yuan"]==base["ending_equity_yuan"]
    result={"scope":"RETROSPECTIVE CASH-DIVIDEND SENSITIVITY; NOT FILL-VERIFIED OR BLIND",
            "source_sha256":{Path(f).name:hashlib.sha256(Path(f).read_bytes()).hexdigest()
                             for f in [args.audit,args.events,*args.daily]},
            "baseline":base,"gross_dividend":gross,"tax20_sensitivity":tax20}
    Path(args.out).write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print({k:v["ending_equity_yuan"] for k,v in [("baseline",base),("gross",gross),("tax20",tax20)]})

if __name__=="__main__":main()
