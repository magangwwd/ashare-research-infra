#!/usr/bin/env python3
"""Append-only BTC V3 DAILY shadow-paper ledger. No exchange connections/orders.

Runs against original Binance UTC daily bars AFTER they close. Signal/fill
events are modeled at UTC daily open from the previous day's close, then
recorded only after the full UTC day ends. This is causal EOD shadow replay,
NOT a live-timed fill, live execution or an OKX simulated order.
"""
from __future__ import annotations
import argparse
import csv
from datetime import date,datetime,timedelta,timezone
import hashlib
import importlib.util
import io
import json
import math
from pathlib import Path

FREEZE_CUTOFF=date(2026,10,14)
FORWARD_FIRST=date(2026,10,15)
FORWARD_END_EXCLUSIVE=date(2027,1,7)
FEE_SLIP=0.0015
COLUMNS=("date","open","high","low","close","volume")

def canon_hash(rows):
    buf=io.StringIO(newline="")
    w=csv.DictWriter(buf,fieldnames=COLUMNS)
    w.writeheader()
    for r in rows:
        w.writerow({k:r[k] for k in COLUMNS})
    return hashlib.sha256(buf.getvalue().encode("utf-8")).hexdigest()

def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf8"))

def freeze_engine(path):
    spec=importlib.util.spec_from_file_location("btc_v3_immutable_daily_engine",path)
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def genesis(config,rows):
    idx=next((i for i,r in enumerate(rows) if r["date"]==FREEZE_CUTOFF),None)
    if idx is None:
        raise ValueError("2026-10-14 historical cutoff missing")
    return {
      "schema":"BTC_V3_FORWARD_DAILY_SHADOW_V1",
      "strategy_id":config["strategy_id"],
      "frozen_commit":config["frozen_git_commit"],
      "state_asof_utc":FREEZE_CUTOFF.isoformat(),
      "cash_usdt":1000.0,"btc_qty":0.0,"nav_usdt":1000.0,
      "hwm_usdt":1000.0,"drawdown":0.0,
      "risk_state":"NORMAL","weekly_target_weight":0.0,
      "trades_cumulative":0,"friction_cumulative_usdt":0.0,
      "data_prefix_sha256":canon_hash(rows[:idx+1]),
      "state_kind":"GENESIS_AFTER_HISTORICAL_BACKFILL",
      "fill_kind":"NONE",
      "event":None,
      "last_signal_inputs_through_utc":FREEZE_CUTOFF.isoformat()
    }

def step(prev,rows,mod,config):
    current_date=date.fromisoformat(prev["state_asof_utc"])+timedelta(days=1)
    if not(FORWARD_FIRST<=current_date<FORWARD_END_EXCLUSIVE):
        raise ValueError("day outside frozen forward monitoring window")
    i=next((i for i,r in enumerate(rows) if r["date"]==current_date),None)
    if i is None or i<181:
        raise ValueError("missing actual next UTC candle")
    before=rows[i-1]
    if before["date"]!=current_date-timedelta(days=1):
        raise ValueError("not immediately successive day")
    if canon_hash(rows[:i])!=prev["data_prefix_sha256"]:
        raise ValueError("HISTORICAL DAILY PREFIX CHANGED: refuse forward replay")

    state=prev["risk_state"]
    if state not in ("NORMAL","REDUCED","HALTED"):
        raise ValueError("unknown frozen risk state")
    dd=float(prev["drawdown"])
    state_before=state
    enter_reduced=False
    if state=="HALTED" or dd>=.15:
        state="HALTED"
    elif state=="NORMAL" and dd>=.08:
        state="REDUCED"
        enter_reduced=True
    elif state=="REDUCED" and current_date.weekday()==0 and dd<=.04:
        state="NORMAL"

    # Compute Monday's target from PRIOR fully closed Sunday candle only.
    weekly_target=float(prev["weekly_target_weight"])
    if current_date.weekday()==0 and state!="HALTED":
        weekly_target=mod.weekly_weight(rows,i)
    if not math.isfinite(weekly_target) or not(0<=weekly_target<=.5):
        raise ValueError("bad frozen weekly target")

    target=0.0 if state=="HALTED" else (min(weekly_target*.5,.25) if state=="REDUCED" else weekly_target)
    cash=float(prev["cash_usdt"])
    btc=float(prev["btc_qty"])
    op=float(rows[i]["open"])
    close=float(rows[i]["close"])
    value_open=cash+btc*op
    if not value_open>0:
        raise ValueError("account insolvent")
    weight=btc*op/value_open
    rebalance=current_date.weekday()==0 and abs(weight-target)>=.05-1e-12
    # Exact zero target is an unconditional liquidation, including tiny tail positions.
    if target<=1e-12 and btc>1e-14:
        rebalance=True
    # Only once when DD crosses 8%; don't chase the halved target every day.
    if enter_reduced and weight>target+1e-12:
        rebalance=True
    if state=="HALTED" and btc>1e-14:
        rebalance=True
    if enter_reduced and current_date.weekday()!=0 and weight<target:
        rebalance=False
    trade=None
    friction=0.0
    if rebalance:
        diff=target*value_open-btc*op
        if diff>1e-10:
            units=min(diff/(op*(1+FEE_SLIP)),cash/(op*(1+FEE_SLIP)))
            if units>1e-14:
                cash-=units*op*(1+FEE_SLIP)
                btc+=units
                side="BUY"
            else:
                units=0.0
        else:
            units=min(btc,-diff/op)
            if units>1e-14:
                cash+=units*op*(1-FEE_SLIP)
                btc-=units
                side="SELL"
            else:
                units=0.0
        if units>1e-14:
            friction=units*op*FEE_SLIP
            trade={"side":side,"quantity_btc":units,"modeled_fill_open_price":op,
                   "friction_usdt":friction,"desired_btc_weight":target,
                   "reason":("PERMANENT_HALT" if state=="HALTED" else
                             "FIRST_8PCT_RISK_CUT" if enter_reduced else
                             "ZERO_TARGET_LIQUIDATION" if target==0 else "WEEKLY_REBALANCE"),
                   "fill_is_a_model_not_observed_live_execution":True}

    nav=cash+btc*close
    hwm=max(float(prev["hwm_usdt"]),nav)
    new_dd=(hwm-nav)/hwm
    if not math.isfinite(nav) or nav<=0 or new_dd<0:
        raise ValueError("invalid NAV")
    return {
      "schema":prev["schema"],"strategy_id":prev["strategy_id"],
      "frozen_commit":prev["frozen_commit"],
      "state_asof_utc":current_date.isoformat(),
      "cash_usdt":cash,"btc_qty":btc,"nav_usdt":nav,
      "hwm_usdt":hwm,"drawdown":new_dd,
      "risk_state":state,"risk_state_before":state_before,
      "weekly_target_weight":weekly_target,
      "target_weight_after_risk":target,
      "trades_cumulative":int(prev["trades_cumulative"])+(1 if trade else 0),
      "friction_cumulative_usdt":float(prev["friction_cumulative_usdt"])+friction,
      "data_prefix_sha256":canon_hash(rows[:i+1]),
      "state_kind":"PROSPECTIVE_DAILY_SHADOW_REPLAY",
      "fill_kind":"HYPOTHETICAL_DAILY_UTC_OPEN_RECORDED_AFTER_UTC_DAY_CLOSE",
      "last_signal_inputs_through_utc":before["date"].isoformat(),
      "event":trade,
      "paper_assumption":"Daily archive received AFTER close; entry at today's recorded open is an indicative shadow assumption, NOT independently observed executable quote."
    }

def main():
    p=argparse.ArgumentParser()
    p.add_argument("--config",required=True)
    p.add_argument("--engine",required=True)
    p.add_argument("--data",required=True)
    p.add_argument("--previous")
    p.add_argument("--out",required=True)
    args=p.parse_args()
    config=read_json(args.config)
    if config["frozen_git_commit"]!="d60c667b3654dbfb00efea9bc72241cca3bb8d5c" or \
       config["historical_backfill_cutoff_utc"]!=FREEZE_CUTOFF.isoformat() or \
       config["forward_first_asof_utc"]!=FORWARD_FIRST.isoformat():
        raise ValueError("freeze metadata mismatch")
    module=freeze_engine(args.engine)
    rows=module.load_daily(args.data)
    last_day=rows[-1]["date"]
    if last_day>=datetime.now(timezone.utc).date():
        raise ValueError("data includes an unclosed UTC daily bar")
    if last_day>=FORWARD_END_EXCLUSIVE:
        raise ValueError("forward duration ended; manual evaluation due")
    if not args.previous:
        if last_day!=FREEZE_CUTOFF:
            raise ValueError("Cannot genesis after cutoff: do not backdate a paper start")
        new=genesis(config,rows)
    else:
        prev=read_json(args.previous)
        if prev.get("strategy_id")!=config["strategy_id"] or \
           prev.get("frozen_commit")!=config["frozen_git_commit"] or \
           prev.get("schema")!="BTC_V3_FORWARD_DAILY_SHADOW_V1":
            raise ValueError("forward state freeze identifier mismatch")
        prior_day=date.fromisoformat(prev["state_asof_utc"])
        if last_day!=prior_day+timedelta(days=1):
            raise ValueError("PAPER FORWARD GAP/REPLAY RISK: last processed="+str(prior_day)+" newest="+str(last_day)+"; no backdated auto catch-up")
        new=step(prev,rows,module,config)
        new["previous_state_sha256"]=hashlib.sha256(Path(args.previous).read_bytes()).hexdigest()
    new["observed_and_recorded_at_utc"]=datetime.now(timezone.utc).isoformat()
    new["operational_git_sha"]=__import__("os").environ.get("GITHUB_SHA","offline-test")
    out=Path(args.out)
    out.mkdir(parents=True,exist_ok=True)
    dest=out/("btc_v3_forward_"+new["state_asof_utc"].replace("-","")+"__state.json")
    if dest.exists():
        raise ValueError("immutable daily forward record already exists")
    dest.write_text(json.dumps(new,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    print(json.dumps({"kind":new["state_kind"],"asof":new["state_asof_utc"],"nav":new["nav_usdt"],
                      "risk_state":new["risk_state"],"btc_qty":new["btc_qty"],
                      "trades":new["trades_cumulative"],"output":str(dest)},ensure_ascii=False))

if __name__=="__main__":
    main()
