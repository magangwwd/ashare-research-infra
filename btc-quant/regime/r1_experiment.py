#!/usr/bin/env python3
"""Router R0/R1 research: rolling 730/90 folds, causal range-break guard, ONE shared account.

ONLY historical daily-close -> next archived daily-open proxy. NOT forward fills,
NOT intraday grid, NOT authorization for live HMG. Training coverage determines
per-fold HMG research eligibility; thresholds and mapping are pre-registered.
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
from datetime import timedelta
from pathlib import Path


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = result
    spec.loader.exec_module(result)
    return result


ROUTER = module("r1_router", Path(__file__).with_name("router.py"))


def write_csv(path, data, default_fields=("date",)):
    fields = list(dict.fromkeys(k for row in data for k in row))
    with Path(path).open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields or list(default_fields))
        writer.writeheader()
        writer.writerows(data)


def create_folds(rows, states, train_days=730, test_days=90):
    # 180-day indicators and 730-day previous realized-vol percentile history
    # require >790 source bars. Additional buffer yields 730 regime observations.
    first = 180 + train_days
    by_day = {x["date"]: x["confirmed_regime"] for x in states}
    folds = []
    for start in range(first, len(rows), test_days):
        end = min(len(rows), start + test_days)
        training = [by_day.get(rows[i]["date"].isoformat()) for i in range(start-train_days, start)]
        counts = {s: training.count(s) for s in set(training) if s}
        episodes = {}
        prev = None
        for s in training:
            if s is not None and s != prev:
                episodes[s] = episodes.get(s, 0) + 1
            prev = s
        hmg_eligible = counts.get("RANGE", 0) >= 120 and episodes.get("RANGE", 0) >= 3
        folds.append({
            "fold": len(folds) + 1, "start_i": start, "end_i": end,
            "train_start": rows[start-train_days]["date"].isoformat(),
            "train_end": rows[start-1]["date"].isoformat(),
            "test_start": rows[start]["date"].isoformat(),
            "test_end": rows[end-1]["date"].isoformat(),
            "training_range_observations": counts.get("RANGE", 0),
            "training_range_episodes": episodes.get("RANGE", 0),
            "hmg_research_eligible": hmg_eligible,
            "mapping_selection": "PRE_REGISTERED_COVERAGE_ONLY_NOT_OPTIMIZED",
        })
    return folds


def weekly_target(rows, i):
    j = i-1
    if j < 180:
        return 0.0
    prices = [r["close"] for r in rows]
    score = sum(prices[j] > prices[j-k] for k in (30, 90, 180)) / 3
    ret = [math.log(prices[k]/prices[k-1]) for k in range(j-59, j+1)]
    sigma = statistics.stdev(ret) * math.sqrt(365)
    return score * min(.5, .1/sigma) if sigma > 0 and math.isfinite(sigma) else 0.0


def volatility_warning(rows, j, lower, atr_usdt):
    # All reference data exclude the signal candle j.
    if j < 65 or rows[j]["close"] > lower + .5*atr_usdt:
        return False
    baseline = statistics.mean(r["volume"] for r in rows[j-20:j])
    relative_volume = rows[j]["volume"]/baseline if baseline > 0 else 0.0
    close = [r["close"] for r in rows]
    returns = [math.log(close[k]/close[k-1]) for k in range(j-59, j+1)]
    recent = statistics.stdev(returns[-20:])
    long = statistics.stdev(returns)
    return relative_volume >= 1.5 or (long > 0 and recent/long >= 1.4)


def simulate(rows, states, folds, variant, cost_mult):
    if variant not in ("R0", "R1_PRICE", "R1"):
        raise ValueError(variant)
    cost = .0015*cost_mult
    state = {r["date"]: r["confirmed_regime"] for r in states}
    feature = {f.date: f for f in ROUTER.compute_features(rows)}
    fold_by_i = {i: f for f in folds for i in range(f["start_i"], f["end_i"])}
    cash, btc, hwm, risk = 1000.0, 0.0, 1000.0, "NORMAL"
    active, layer, anchor, spacing, budget, avg_cost = "CASH", 0, None, None, 0.0, None
    fixed_lower, fixed_atr, below, watch, recovered = None, None, 0, "NONE", 0
    last_dd, nav_before_test = 0.0, 1000.0
    curve, trades, events, fold_stats = [], [], [], []
    previous_fold = None
    weights = (1.0, 1.15, 1.25, 1.35, 1.50)

    def current_nav(px):
        return cash + btc*px

    def trade(i, side, qty, reason, signal_date):
        nonlocal cash, btc
        if qty < 1e-13:
            return False
        px = rows[i]["open"]
        if side == "BUY":
            qty = min(qty, cash/(px*(1+cost)))
            if qty < 1e-13:
                return False
            cash -= qty*px*(1+cost)
            btc += qty
        else:
            qty = min(qty, btc)
            if qty < 1e-13:
                return False
            cash += qty*px*(1-cost)
            btc -= qty
        if btc < 1e-13:
            btc = 0.0
        trades.append({"signal_asof": signal_date, "fill_date": rows[i]["date"].isoformat(),
                       "side": side, "btc_qty": qty, "open_price": px,
                       "cost_usdt": qty*px*cost, "reason": reason,
                       "regime": active, "risk": risk, "variant": variant})
        return True

    def reset_grid():
        nonlocal layer, anchor, spacing, budget, avg_cost
        layer, anchor, spacing, budget, avg_cost = 0, None, None, 0.0, None

    def close_position(i, reason, signal):
        nonlocal active
        done = trade(i, "SELL", btc, reason, signal)
        if done:
            reset_grid()
            active = "CASH"
        return done

    for i in range(folds[0]["start_i"], len(rows)):
        fold = fold_by_i.get(i)
        if fold is None:
            continue
        prev = rows[i-1]
        d = prev["date"].isoformat()
        cur = rows[i]
        px = cur["open"]
        f = feature.get(d)
        g = state.get(d, "TRANSITION")
        closed_nav = current_nav(prev["close"])
        hwm = max(hwm, closed_nav)
        last_dd = (hwm-closed_nav)/hwm
        if previous_fold != fold["fold"]:
            if previous_fold is not None:
                fold_stats[-1]["end_nav"] = curve[-1]["nav"]
            fold_stats.append({"fold": fold["fold"], "test_start": fold["test_start"],
                               "test_end": fold["test_end"], "start_nav": curve[-1]["nav"] if curve else nav_before_test,
                               "hmg_research_eligible": fold["hmg_research_eligible"],
                               "train_start": fold["train_start"], "train_end": fold["train_end"]})
            previous_fold = fold["fold"]

        # The account high-water mark, cash, BTC, risk and grid state persist
        # across refits/folds. Emergency exits bypass ordinary Monday clocks.
        reduced_now = False
        if risk != "HALTED":
            if last_dd >= .15:
                risk = "HALTED"
                close_position(i, "GLOBAL_HALT_15PCT", d)
            elif risk == "NORMAL" and last_dd >= .08:
                risk = "REDUCED"
                reduced_now = True
                trade(i, "SELL", btc*.5, "GLOBAL_REDUCE_8PCT", d)
            elif risk == "REDUCED" and cur["date"].weekday() == 0 and last_dd <= .04:
                risk = "NORMAL"

        exited = False
        if risk == "HALTED" and btc:
            exited = close_position(i, "GLOBAL_HALT_REMAINDER", d)

        # Frozen basket lower boundary never follows a breakdown lower.
        if variant != "R0" and fixed_lower is not None:
            if prev["close"] < fixed_lower:
                below += 1
                recovered = 0
                if watch == "NONE":
                    watch = "WATCH"
                    events.append({"signal_asof": d, "event": "WATCH", "fixed_lower": fixed_lower,
                                   "signal_close": prev["close"], "variant": variant,
                                   "next_execution_day": cur["date"].isoformat()})
                if prev["close"] < fixed_lower - .5*fixed_atr or below >= 2:
                    watch = "EXIT_PENDING"
                    events.append({"signal_asof": d, "event": "EXIT_PENDING",
                                   "fixed_lower": fixed_lower, "next_execution_day": cur["date"].isoformat(),
                                   "variant": variant})
            elif watch in ("WATCH", "EXIT_PENDING"):
                below = 0
                recovered += 1
                if recovered >= 3 and not volatility_warning(rows, i-1, fixed_lower, fixed_atr):
                    watch, recovered, fixed_lower, fixed_atr = "NONE", 0, None, None
            elif variant == "R1" and volatility_warning(rows, i-1, fixed_lower, fixed_atr):
                watch = "WATCH"
                events.append({"signal_asof": d, "event": "PRE_BREAK_WATCH_VOLUME_VOL",
                               "fixed_lower": fixed_lower, "variant": variant})
            if watch == "EXIT_PENDING" and btc and risk != "HALTED":
                exited = close_position(i, "RANGE_BREAKDOWN_EXIT", d) or exited
                events.append({"signal_asof": d, "event": "EXECUTED_EXIT",
                               "execution_date": cur["date"].isoformat(), "variant": variant})

        eligible = bool(fold["hmg_research_eligible"])
        requested = ("HMG" if g == "RANGE" and eligible else
                     "V3" if g == "TREND_UP" else
                     "V3_DEFENSIVE" if g == "RECOVERY" else "CASH")
        if risk == "HALTED" or (risk == "REDUCED" and requested == "HMG"):
            requested = "CASH"
        if requested == "HMG" and watch != "NONE":
            requested = "CASH" if watch == "EXIT_PENDING" else "HMG_BLOCKED_ADDS"

        if btc and active != requested:
            if requested not in ("HMG", "HMG_BLOCKED_ADDS") or active not in ("HMG", "HMG_BLOCKED_ADDS"):
                exited = close_position(i, "REGIME_OR_MAPPING_EXIT", d) or exited
            elif requested == "HMG_BLOCKED_ADDS":
                active = "HMG_BLOCKED_ADDS"
        if requested == "CASH" and btc:
            exited = close_position(i, "REQUESTED_CASH_EXIT", d) or exited

        if risk != "HALTED" and not reduced_now and not exited and requested in ("V3", "V3_DEFENSIVE"):
            if cur["date"].weekday() == 0:
                base_target = weekly_target(rows, i)
                desired = min(base_target, .25) if requested == "V3_DEFENSIVE" else base_target
                if risk == "REDUCED":
                    desired = min(.5*desired, .25)
                target_usdt = desired*current_nav(px)
                diff = target_usdt-btc*px
                if abs(diff)/max(current_nav(px), 1e-9) >= .05 or (desired == 0 and btc):
                    trade(i, "BUY" if diff > 0 else "SELL",
                          diff/(px*(1+cost)) if diff > 0 else -diff/px,
                          "V3_MONDAY_REBALANCE", d)
            active = requested
        elif risk == "NORMAL" and requested == "HMG" and not exited and f is not None:
            if btc == 0 and watch == "NONE":
                # Reference lower uses only candles prior to signal candle.
                if i-1 < 30:
                    continue
                lower = min(x["low"] for x in rows[i-31:i-1])
                atr = f.atr14_pct*prev["close"]
                full_budget = min(current_nav(px)*.25, cash)
                first_usdt = full_budget * weights[0]/sum(weights)
                if trade(i, "BUY", first_usdt/(px*(1+cost)), "HMG_INITIAL", d):
                    fixed_lower, fixed_atr = lower, atr
                    below, recovered, watch = 0, 0, "NONE"
                    anchor, spacing, budget = px, max(.018, min(.9*f.atr14_pct, .035)), full_budget
                    layer, avg_cost, active = 1, px*(1+cost), "HMG"
            elif btc > 0 and active in ("HMG", "HMG_BLOCKED_ADDS") and layer < 5:
                if prev["close"] <= anchor*(1-spacing*layer):
                    allowance = budget*weights[layer]/sum(weights)
                    qty = min(allowance, cash)/(px*(1+cost))
                    old_qty = btc
                    if trade(i, "BUY", qty, "HMG_LAYER", d):
                        avg_cost = (avg_cost*old_qty + px*(1+cost)*(btc-old_qty))/btc
                        layer += 1
                        active = "HMG"
            if btc > 0 and active in ("HMG", "HMG_BLOCKED_ADDS") and avg_cost is not None:
                # 0.8% is GROSS exit trigger, not net realized profit.
                if prev["close"] >= avg_cost*1.008:
                    close_position(i, "HMG_GROSS_TP_NEXT_OPEN", d)
        elif requested == "HMG_BLOCKED_ADDS" and active == "HMG":
            active = "HMG_BLOCKED_ADDS"

        nav = current_nav(cur["close"])
        hwm = max(hwm, nav)
        dd = (hwm-nav)/hwm
        curve.append({"date": cur["date"].isoformat(), "nav": nav,
                      "cash_usdt": cash, "btc_qty": btc, "btc_weight": btc*cur["close"]/nav if nav else 0,
                      "drawdown": dd, "high_water_mark": hwm, "account_risk_state": risk,
                      "confirmed_regime_asof": g, "requested_strategy": requested,
                      "executed_strategy": active if btc else "CASH",
                      "range_breakdown_risk": watch, "layers": layer, "fold": fold["fold"]})
    if fold_stats:
        fold_stats[-1]["end_nav"] = curve[-1]["nav"]
    for entry in fold_stats:
        entry["net_return"] = entry["end_nav"]/entry["start_nav"] - 1
        part = [p for p in curve if p["fold"] == entry["fold"]]
        entry["test_max_drawdown"] = max((p["drawdown"] for p in part), default=0)
    navs = [x["nav"] for x in curve]
    rets = [math.log(navs[i]/navs[i-1]) for i in range(1,len(navs))]
    stdev = statistics.stdev(rets) if len(rets)>1 else 0
    cagr = (navs[-1]/1000)**(365.25/len(navs))-1
    result = {
        "variant": variant, "cost_multiple": cost_mult, "initial_usdt": 1000.0,
        "final_nav": navs[-1], "total_return": navs[-1]/1000-1, "cagr": cagr,
        "max_drawdown": max(x["drawdown"] for x in curve),
        "annualized_volatility": stdev*math.sqrt(365),
        "sharpe_rf_zero": statistics.mean(rets)/stdev*math.sqrt(365) if stdev else None,
        "trades": len(trades), "total_friction_usdt": sum(x["cost_usdt"] for x in trades),
        "max_grid_layers": max(x["layers"] for x in curve),
        "ever_halted": any(x["account_risk_state"] == "HALTED" for x in curve),
        "breaking_risk_events": len(events), "folds": len(folds),
        "historical_oos_protocol": "rolling-730-training-coverage/90-day-test; no fitted-profit mapping",
        "hmg_status": "DAILY_PROXY_RESEARCH_NOT_INTRADAY_NOT_APPROVED",
        "execution_warning": "Next archived UTC open is a historical modeled fill, never an actionable retrospective order",
    }
    return result, curve, trades, events, fold_stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    rows = ROUTER.load_daily(args.input)
    cfg = ROUTER.load_config(args.config)
    states = ROUTER.smooth_regimes(ROUTER.compute_features(rows), cfg)
    folds = create_folds(rows, states)
    if not folds:
        raise ValueError("INSUFFICIENT_DATA_FOR_730_90")
    dest = Path(args.out)
    dest.mkdir(parents=True, exist_ok=True)
    write_csv(dest/"fold_boundaries.csv", folds)
    results = {}
    for cost_mult in (1, 2):
        for version in ("R0", "R1_PRICE", "R1"):
            tag = version.lower() + "_" + str(cost_mult) + "x"
            summary, equity, trades, events, fold_stats = simulate(rows, states, folds, version, cost_mult)
            write_csv(dest/(tag+"_equity.csv"), equity)
            write_csv(dest/(tag+"_trades.csv"), trades, ("signal_asof", "fill_date", "reason"))
            write_csv(dest/(tag+"_events.csv"), events, ("signal_asof", "event"))
            write_csv(dest/(tag+"_folds.csv"), fold_stats)
            results[tag] = summary
    manifest = {"schema": "BTC_ROUTER_STAGE1_R1_20261017",
                "research_only": True, "live_trading": False,
                "input_sha256": hashlib.sha256(Path(args.input).read_bytes()).hexdigest(),
                "data_end_utc": rows[-1]["date"].isoformat(),
                "first_test_date": folds[0]["test_start"], "last_test_date": folds[-1]["test_end"],
                "folds": len(folds), "fold_train_days": 730, "fold_test_days": 90,
                "pre_registered": True, "param_fitting": "NONE; train range coverage eligibility only",
                "warning": "Retrospective OOS folds, NOT independent forward evidence",
                "results": results}
    (dest/"stage1_summary.json").write_text(json.dumps(manifest,indent=2,ensure_ascii=False)+"\n",encoding="utf8")
    print(json.dumps({"data_end_utc":manifest["data_end_utc"],"folds":len(folds),"results":results},ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()
