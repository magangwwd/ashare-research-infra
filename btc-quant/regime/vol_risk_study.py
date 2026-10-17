#!/usr/bin/env python3
"""BTC volatility allocation risk-preservation study, research only.

Evaluate pre-registered protective overlays against exact VOL_STATIC/V3_STATIC
same-ledger benchmarks. 730/90 retrospective rolling tests, daily closed UTC
data, next historical open modeled fills. No API keys, real orders, leverage.
Frozen V3 engine/config/forward account are never modified.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import math
import statistics
import sys
from pathlib import Path

STUDY_ID = "BTC_VOL_RISK_STUDY_20261017"
VARIANTS = ("VOL_BASE", "VOL_CAP35", "VOL_SCALE75", "VOL_SHOCK20",
            "VOL_DD5", "VOL_COMBO", "VOL_TRAIN_RISK_MATCH")
FEE_SLIP = .0015
INITIAL_CASH = 1000.0


def load_module(name, filename):
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


STAGE = load_module("btc_vol_study_previous_stage", "r1_experiment.py")


def save_csv(path, records, fallback):
    fields = list(dict.fromkeys(k for x in records for k in x))
    with Path(path).open("w", newline="", encoding="utf8") as out:
        writer = csv.DictWriter(out, fieldnames=fields or fallback)
        writer.writeheader()
        writer.writerows(records)


def annual_vol(x):
    return statistics.stdev(x)*math.sqrt(365) if len(x)>1 else 0.0


def features(rows):
    closes = [r["close"] for r in rows]
    logret = [0.0] + [math.log(closes[i]/closes[i-1]) for i in range(1,len(rows))]
    sigma60, sigma20, base, v3 = [], [], [], []
    for i in range(len(rows)):
        j = i-1  # signals only use PRIOR UTC completed close
        if j < 180:
            sigma60.append(0.0)
            sigma20.append(0.0)
            base.append(0.0)
            v3.append(0.0)
            continue
        sig = annual_vol(logret[j-59:j+1])
        small = annual_vol(logret[j-19:j+1])
        w = min(.5,.10/sig) if sig > 0 and math.isfinite(sig) else 0.0
        score = sum(closes[j] > closes[j-k] for k in (30,90,180)) / 3
        sigma60.append(sig)
        sigma20.append(small)
        base.append(w)
        v3.append(score*w)
    return {"close":closes, "ret":logret, "sigma60":sigma60,
            "sigma20":sigma20, "vol_weight":base, "v3_weight":v3}


def train_only_scales(rows, folds, f):
    """Fold risk scale from TRAINING-ONLY close-return-weight proxy.

    These realized proxies exclude actual transaction costs and account stops;
    the resulting candidate is not guaranteed equal observed risk to V3.
    """
    scales = {}
    records = []
    for fold in folds:
        start = fold["start_i"]
        begin = start-730
        vol_pnl = [f["vol_weight"][k]*f["ret"][k] for k in range(begin+1,start)]
        v3_pnl = [f["v3_weight"][k]*f["ret"][k] for k in range(begin+1,start)]
        risk_vol = annual_vol(vol_pnl)
        risk_v3 = annual_vol(v3_pnl)
        scale = min(1.0,max(.30,risk_v3/risk_vol)) if risk_vol>0 else 0.30
        scales[fold["fold"]] = scale
        records.append({"fold":fold["fold"],"train_start":fold["train_start"],
                        "train_end":fold["train_end"],
                        "estimated_vol_baseline_annvol":risk_vol,
                        "estimated_v3_annvol":risk_v3,
                        "training_only_scale":scale,
                        "risk_match_caveat":"close-weight proxy; actual test risk may differ"})
    return scales, records


def policy_target(name, i, f, scale, drawdown_soft):
    w = f["vol_weight"][i]
    if name == "VOL_CAP35":
        w = min(w,.35)
    elif name == "VOL_SCALE75":
        w *= .75
    elif name == "VOL_TRAIN_RISK_MATCH":
        w *= scale
    if name in ("VOL_SHOCK20","VOL_COMBO"):
        ratio = f["sigma20"][i]/f["sigma60"][i] if f["sigma60"][i]>0 else 0.0
        if ratio >= 1.50:
            w *= .50
    if name in ("VOL_DD5","VOL_COMBO") and drawdown_soft:
        w = min(w,.25)
    return min(max(w,0.0),.50)


def simulate(rows, folds, f, name, cost_mult, scales):
    if name not in VARIANTS or cost_mult not in (1,2):
        raise ValueError("invalid experiment")
    cost = FEE_SLIP*cost_mult
    first = folds[0]["start_i"]
    fold_at = {i:fold for fold in folds for i in range(fold["start_i"],fold["end_i"])}
    cash, btc, highwater, risk = INITIAL_CASH, 0.0, INITIAL_CASH, "NORMAL"
    soft_dd, exposure_before = False, 0.0
    trades, equity, fold_stats, events = [],[],[],[]
    prev_fold = None
    dds = []
    for i in range(first,len(rows)):
        fold = fold_at.get(i)
        if fold is None:
            break
        prev = rows[i-1]
        day = rows[i]
        asof = prev["date"].isoformat()
        now = day["date"].isoformat()
        px = day["open"]
        is_monday = day["date"].weekday() == 0
        if prev_fold != fold["fold"]:
            if fold_stats:
                fold_stats[-1]["end_nav"] = equity[-1]["nav"]
            fold_stats.append({"fold":fold["fold"],"test_start":fold["test_start"],
                               "test_end":fold["test_end"],
                               "train_end":fold["train_end"],
                               "start_nav":equity[-1]["nav"] if equity else INITIAL_CASH,
                               "training_risk_scale":scales[fold["fold"]]})
            prev_fold=fold["fold"]
        def nav(price):
            return cash+btc*price
        def execute(side,quantity,reason,override=False):
            nonlocal cash,btc
            if quantity <= 1e-13:
                return
            if side=="BUY":
                quantity = min(quantity,cash/(px*(1+cost)))
                if quantity <= 1e-13:
                    return
                cash -= quantity*px*(1+cost)
                btc += quantity
            else:
                quantity=min(quantity,btc)
                if quantity <= 1e-13:
                    return
                cash += quantity*px*(1-cost)
                btc -= quantity
                if btc<1e-13:
                    btc=0.0
            trades.append({"signal_asof":asof,"fill_date":now,
                           "side":side,"btc_qty":quantity,"price":px,
                           "friction_usdt":quantity*px*cost,
                           "reason":reason,"variant":name,
                           "cost_multiple":cost_mult,
                           "emergency":override,"fold":fold["fold"]})
        observed_nav = nav(prev["close"])
        highwater=max(highwater,observed_nav)
        dd = (highwater-observed_nav)/highwater if highwater>0 else 0.0
        reduced_now = False
        if risk != "HALTED":
            if dd >= .15:
                risk="HALTED"
                execute("SELL",btc,"HALT_15PCT",True)
                events.append({"signal_asof":asof,"event":"HALT","drawdown":dd})
            elif risk=="NORMAL" and dd>=.08:
                risk="REDUCED"
                reduced_now=True
                execute("SELL",btc*.5,"REDUCE_8PCT",True)
                events.append({"signal_asof":asof,"event":"REDUCE","drawdown":dd})
            elif risk=="REDUCED" and is_monday and dd<=.04:
                risk="NORMAL"
                events.append({"signal_asof":asof,"event":"RECOVER_GLOBAL","drawdown":dd})
        if name in ("VOL_DD5","VOL_COMBO") and risk != "HALTED":
            if not soft_dd and dd>=.05:
                soft_dd=True
                events.append({"signal_asof":asof,"event":"SOFT_DD_ENTER","drawdown":dd})
            elif soft_dd and is_monday and dd<=.03:
                soft_dd=False
                events.append({"signal_asof":asof,"event":"SOFT_DD_RECOVER","drawdown":dd})
        if risk == "HALTED":
            if btc:
                execute("SELL",btc,"HALT_REMAINDER",True)
        else:
            # Emergency soft-DD exposure cut runs immediately at next open;
            # it may ONLY decrease risk, never add on an emergency.
            if soft_dd and btc and btc*px/nav(px)>.25+1e-12:
                desired=.25*nav(px)
                execute("SELL",(btc*px-desired)/px,"SOFT_DD_25PCT_CAP",True)
            if is_monday and not reduced_now:
                target=policy_target(name,i,f,scales[fold["fold"]],soft_dd)
                if risk=="REDUCED":
                    target=min(target*.5,.25)
                open_nav=nav(px)
                diff=target*open_nav-btc*px
                current_weight=btc*px/open_nav if open_nav>0 else 0.0
                if abs(current_weight-target) >= .05-1e-12 or (target==0 and btc>1e-13):
                    qty=diff/(px*(1+cost)) if diff>0 else -diff/px
                    # REDUCED and emergencies do not increase risk between clocks.
                    execute("BUY" if diff>0 else "SELL",abs(qty),"WEEKLY_VOL_REBALANCE")
        endnav=nav(day["close"])
        highwater=max(highwater,endnav)
        enddd=(highwater-endnav)/highwater if highwater>0 else 0.0
        dds.append(enddd)
        equity.append({"date":now,"nav":endnav,"cash_usdt":cash,
                       "btc_qty":btc,"btc_weight":btc*day["close"]/endnav if endnav>0 else 0,
                       "drawdown":enddd,"high_water_mark":highwater,
                       "risk_state":risk,"soft_drawdown_guard":soft_dd,
                       "fold":fold["fold"],"name":name,"cost_multiple":cost_mult})
    if fold_stats:
        fold_stats[-1]["end_nav"]=equity[-1]["nav"]
    for fold in fold_stats:
        fold["net_return"]=fold["end_nav"]/fold["start_nav"]-1
        samples=[r for r in equity if r["fold"]==fold["fold"]]
        fold["max_drawdown_from_global_peak"]=max(x["drawdown"] for x in samples)
        fold["max_btc_weight"]=max(x["btc_weight"] for x in samples)
        fold["friction_usdt"]=sum(t["friction_usdt"] for t in trades if t["fold"]==fold["fold"])
    navs=[x["nav"] for x in equity]
    rets=[math.log(navs[k]/navs[k-1]) for k in range(1,len(navs))]
    v=annual_vol(rets)
    cagr=(navs[-1]/INITIAL_CASH)**(365.25/len(navs))-1
    monthly={}
    for e in equity:
        monthly[e["date"][:7]]=e["nav"]
    first_month={}
    for e in equity:
        first_month.setdefault(e["date"][:7],e["nav"])
    monthly_rets=[monthly[m]/first_month[m]-1 for m in monthly if first_month[m]>0]
    underwater=sum(x["drawdown"]>0 for x in equity)/len(equity)
    summary={"variant":name,"cost_multiple":cost_mult,"start":equity[0]["date"],
             "end":equity[-1]["date"],"folds":len(folds),
             "final_nav":navs[-1],"net_return":navs[-1]/INITIAL_CASH-1,
             "cagr":cagr,"max_drawdown":max(dds),
             "annualized_volatility":v,
             "sharpe_rf_zero":(statistics.mean(rets)*365/v) if v>0 else None,
             "calmar":cagr/max(dds) if max(dds)>0 else None,
             "worst_calendar_month_proxy":min(monthly_rets) if monthly_rets else None,
             "fraction_underwater_days":underwater,
             "mean_btc_weight":statistics.mean(x["btc_weight"] for x in equity),
             "max_btc_weight":max(x["btc_weight"] for x in equity),
             "trades":len(trades),"total_friction_usdt":sum(x["friction_usdt"] for x in trades),
             "ever_halted":any(x["risk_state"]=="HALTED" for x in equity),
             "soft_guard_events":sum(e["event"]=="SOFT_DD_ENTER" for e in events),
             "risk_mode":"spot_only_no_leverage","live_trading":False}
    return summary,equity,trades,events,fold_stats


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--input",required=True)
    ap.add_argument("--out",required=True)
    args=ap.parse_args()
    rows=STAGE.ROUTER.load_daily(args.input)
    cfg=STAGE.ROUTER.load_config(Path(__file__).resolve().parents[1]/"config"/"regime_router_20261016.json")
    states=STAGE.ROUTER.smooth_regimes(STAGE.ROUTER.compute_features(rows),cfg)
    folds=STAGE.create_folds(rows,states)
    if not folds:
        raise ValueError("No valid rolling folds")
    f=features(rows)
    scales,scalerecords=train_only_scales(rows,folds,f)
    out=Path(args.out)
    out.mkdir(parents=True,exist_ok=True)
    save_csv(out/"train_risk_scales.csv",scalerecords,["fold"])
    save_csv(out/"fold_boundaries.csv",folds,["fold"])
    all_results={}
    all_fold_metrics=[]
    curves={}
    for cost_mult in (1,2):
        for name in VARIANTS:
            res,curve,trades,events,fold_result=simulate(rows,folds,f,name,cost_mult,scales)
            label=name.lower()+"_"+str(cost_mult)+"x"
            all_results[label]=res
            curves[label]=curve
            save_csv(out/(label+"__equity.csv"),curve,["date","nav"])
            save_csv(out/(label+"__trades.csv"),trades,["signal_asof","fill_date"])
            save_csv(out/(label+"__events.csv"),events,["signal_asof","event"])
            save_csv(out/(label+"__folds.csv"),fold_result,["fold"])
            all_fold_metrics.extend([dict(x,variant=name,cost_multiple=cost_mult) for x in fold_result])
    # Independent parity control with the previous audited same-ledger baseline:
    # comparing metrics, trades, and full NAV path prevents silent strategy drift.
    parity={}
    for cost_mult in (1,2):
        old,old_curve,old_trades,_,old_folds=STAGE.simulate(rows,states,folds,"VOL_STATIC",cost_mult)
        label="vol_base_"+str(cost_mult)+"x"
        ours=all_results[label]
        max_nav_delta=max(abs(a["nav"]-b["nav"]) for a,b in zip(curves[label],old_curve))
        parity[str(cost_mult)+"x"]={
            "prior_engine_final_nav":old["final_nav"],
            "new_engine_final_nav":ours["final_nav"],
            "max_daily_nav_abs_delta_usdt":max_nav_delta,
            "prior_trades":old["trades"],"new_trades":ours["trades"],
            "pass":len(curves[label])==len(old_curve) and max_nav_delta<1e-6 and
                   ours["trades"]==old["trades"]}
        if not parity[str(cost_mult)+"x"]["pass"]:
            raise AssertionError("VOL baseline fails exact stage1 replay parity: "+str(parity))
    for cost_mult in (1,2):
        v3,_,_,_,_=STAGE.simulate(rows,states,folds,"V3_STATIC",cost_mult)
        all_results["v3_static_"+str(cost_mult)+"x"]={
            "variant":"V3_STATIC","cost_multiple":cost_mult,
            "final_nav":v3["final_nav"],"net_return":v3["total_return"],
            "cagr":v3["cagr"],"max_drawdown":v3["max_drawdown"],
            "annualized_volatility":v3["annualized_volatility"],
            "sharpe_rf_zero":v3["sharpe_rf_zero"],"trades":v3["trades"],
            "total_friction_usdt":v3["total_friction_usdt"],
            "note":"Same-ledger historical comparator; NOT frozen prospective account"}
    # Acceptance gates are evaluated per cost scenario against same-ledger VOL_BASE.
    acceptance={}
    for cost_mult in (1,2):
        baseline=all_results["vol_base_"+str(cost_mult)+"x"]
        for name in VARIANTS[1:]:
            item=all_results[name.lower()+"_"+str(cost_mult)+"x"]
            retain=(item["net_return"]/baseline["net_return"]
                    if baseline["net_return"]>0 else None)
            reduction=(1-item["max_drawdown"]/baseline["max_drawdown"]
                       if baseline["max_drawdown"]>0 else None)
            acceptance[name.lower()+"_"+str(cost_mult)+"x"]={
                "return_retention_fraction":retain,
                "relative_max_drawdown_reduction":reduction,
                "retains_at_least_85pct_gain":retain is not None and retain>=.85,
                "cuts_max_drawdown_at_least_25pct":reduction is not None and reduction>=.25,
                "positive_net_return":item["net_return"]>0,
                "passes_exploratory_two_axis_gate":(
                    retain is not None and reduction is not None and
                    retain>=.85 and reduction>=.25 and item["net_return"]>0),
            }
    save_csv(out/"paired_fold_metrics.csv",all_fold_metrics,["fold","variant"])
    summary={"study_id":STUDY_ID,"research_only":True,"live_trading":False,
             "source_sha256":hashlib.sha256(Path(args.input).read_bytes()).hexdigest(),
             "data_end_utc":rows[-1]["date"].isoformat(),
             "test_start":folds[0]["test_start"],"test_end":folds[-1]["test_end"],
             "folds":len(folds),"initial_usdt":INITIAL_CASH,
             "one_way_friction_1x":FEE_SLIP,"one_way_friction_2x":FEE_SLIP*2,
             "risk_controls":"8pct reduce once, Monday 4pct recovery, 15pct permanent halt",
             "hypotheses":{"VOL_BASE":"exact VOL_STATIC comparator",
                           "VOL_CAP35":"weekly max BTC 35pct",
                           "VOL_SCALE75":"weekly baseline target times 0.75; exposure reference",
                           "VOL_SHOCK20":"on weekly trade, 20d/60d realized vol>=1.5 halves target",
                           "VOL_DD5":"completed-close account DD>=5pct caps BTC at 25pct next open; Monday <=3pct release",
                           "VOL_COMBO":"VOL_SHOCK20 + VOL_DD5",
                           "VOL_TRAIN_RISK_MATCH":"each 90d fold scale based on prior730d 1d-close proxy VOL/V3 risk; scale clamped 0.3..1"},
             "pre_registered_exploratory_gate":"Retain >=85pct of VOL_BASE net profit AND reduce relative maximum drawdown >=25pct at BOTH costs; no live promotion based on retrospective gates",
             "warning":["All historical 2022-2026 observations were already visible; not untouched independent prospective proof.",
                        "Daily close signals model next historical open, no tradable prospective order.",
                        "Training-only V3 risk match uses zero-cost close-weight proxy; actual observed risk differs.",
                        "No parameter tuning on outer folds; 730/90 folds only segment evaluation and train-only risk scale.",
                        "A lower max drawdown at the cost of lower exposure does not prove market timing edge."],
             "parity":parity,"results":all_results,"acceptance":acceptance}
    (out/"vol_risk_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    print(json.dumps({"data_end_utc":summary["data_end_utc"],"parity":parity,
                      "results":{k:{"ret":v["net_return"],"dd":v["max_drawdown"]} for k,v in all_results.items()},
                      "acceptance":acceptance},ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
