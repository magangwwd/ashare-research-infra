"""600351 three-year volatility-expansion regime attribution, NOT a trading system.

The originally frozen volatility_expansion event list is authoritative;
this module never searches for a better expansion threshold. Trend and
signal-day bins are predeclared coarse categories. All labels begin after
the completed daily close, with a separate next-session 10:00 proxy.
This is retrospective exploratory research, NOT an untouched OOS test.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from ai_research.kline_three_year import END, START, TARGET, indicators, read_inputs
from ai_research.kline_15m_bridge import read_minute_partitions, analyze_intraday_minute_rows

VERSION = "KLINE_600351_VOL_REGIME_ATTRIBUTION_V1"
PERIODS = ("discovery", "validation", "test")
TREND_LABELS = ("prior20_down_le_minus5pct", "prior20_sideways", "prior20_up_ge_plus5pct")
DAY_LABELS = ("signal_day_down_le_minus2pct", "signal_day_neutral", "signal_day_up_ge_plus2pct")
NEXT_LABELS = ("next30_down", "next30_flat_or_up")


def bin_trend(r):
    if not np.isfinite(r):
        raise ValueError("Missing prior20 return")
    return TREND_LABELS[0] if r <= -.05 else TREND_LABELS[2] if r >= .05 else TREND_LABELS[1]


def bin_day(r):
    if not np.isfinite(r):
        raise ValueError("Missing signal-day return")
    return DAY_LABELS[0] if r <= -.02 else DAY_LABELS[2] if r >= .02 else DAY_LABELS[1]


def summarize(rows, key="future5_close_close"):
    x=np.array([r[key] for r in rows if r.get(key) is not None],dtype=float)
    if not len(x):
        return {"n":0, "mean":None, "median":None, "up_rate":None,
                "min":None, "max":None, "leave_worst_out_mean":None,
                "leave_best_out_mean":None}
    return {
        "n":int(len(x)),
        "mean":round(float(x.mean()),6),
        "median":round(float(np.median(x)),6),
        "up_rate":round(float(np.mean(x>0)),6),
        "min":round(float(x.min()),6),
        "max":round(float(x.max()),6),
        "leave_worst_out_mean":round(float((x.sum()-x.min())/(len(x)-1)),6)
            if len(x)>1 else None,
        "leave_best_out_mean":round(float((x.sum()-x.max())/(len(x)-1)),6)
            if len(x)>1 else None,
    }


def nonoverlap(rows, spacing=5):
    """Greedy chronological event subsample; not a reconstructed trade ledger."""
    picked=[]
    last_idx=-10**9
    for row in sorted(rows,key=lambda v:v["signal_date"]):
        if row["exchange_session_idx"]-last_idx>spacing:
            picked.append(row)
            last_idx=row["exchange_session_idx"]
    return picked


def analyze(daily, baseline, raw_minutes):
    if baseline.get("authorization")!="RESEARCH_ONLY_NO_TRADING":
        raise ValueError("Unknown evidence packet or trading authorization")
    if baseline.get("symbol")!=TARGET or baseline.get("as_of")!=str(END):
        raise ValueError("Wrong symbol/as-of evidence")
    if baseline.get("status")!="EXPLORATORY_COVERAGE_PASSED_NOT_PROVEN":
        raise ValueError("Minute source not previously validated")
    if baseline.get("minute_valid_days_aligned")!=728 or baseline.get("minute_daily_coverage")<.99:
        raise ValueError("Minute coverage declined")
    events=[r for r in baseline["signal_details"] if r["pattern"]=="volatility_expansion"]
    if len(events)!=31 or len({r["signal_date"] for r in events})!=31:
        raise ValueError("Original 31 frozen volatility events missing/duplicated")
    known={x:"discovery" if x<="2024-12-31" else
           "validation" if x<="2025-12-31" else "test" for x in
           [r["signal_date"] for r in events]}
    if {p:sum(r["period"]==p for r in events) for p in PERIODS} != {
            "discovery":15,"validation":10,"test":6}:
        raise ValueError("Unrecognized original temporal events")
    minute, qa=analyze_intraday_minute_rows(raw_minutes)
    if len(minute)!=728 or qa["rejected_days"] or qa["duplicate_bars"]:
        raise ValueError("Detailed minute source no longer covers all 728 validated days")
    rows_10=raw_minutes.loc[raw_minutes["ts_code"].eq(TARGET)].copy()
    rows_10["ts"]=pd.to_datetime(rows_10["trade_time"])
    rows_10=rows_10.loc[rows_10.ts.dt.strftime("%H:%M").eq("10:00")]
    if rows_10["ts"].dt.normalize().duplicated().any() or len(rows_10)!=728:
        raise ValueError("Missing/duplicate verified 10:00 bar-end price")
    price_10=rows_10.set_index(rows_10.ts.dt.normalize())["close"].astype(float)
    p,marks=indicators(daily)
    sessions=list(p.index)
    index={d:i for i,d in enumerate(sessions)}
    close=p.adj_close
    day_return=close/close.shift(1)-1
    # Prior 20 completed sessions, excludes the entire signal session.
    trend20=close.shift(1)/close.shift(21)-1
    outlook=close.shift(-5)/close-1
    rows=[]
    for event in events:
        d=pd.Timestamp(event["signal_date"])
        i=index.get(d)
        if i is None or i+5>=len(sessions):
            raise ValueError("Exchange session missing or +5 outside frozen horizon")
        if not bool(marks["volatility_expansion"].loc[d]):
            raise ValueError("Event no longer matches frozen daily volatility definition")
        if (not np.isfinite(outlook.loc[d]) or
            abs(outlook.loc[d]-event["future_5d_close_to_close_return"])>1.2e-5):
            raise ValueError("Daily adjusted return inconsistent with archived 15m study")
        if not pd.notna(p.loc[sessions[i+1],"open"]) or not pd.notna(
            p.loc[sessions[i+1],"adjustment_factor"]):
            raise ValueError("Next-day actual opening price missing")
        nxt=sessions[i+1]
        nx_open=float(p.loc[nxt,"open"]*p.loc[nxt,"adjustment_factor"])
        close_t5=float(close.iloc[i+5])
        nx_first30=event.get("next_session_first_30m_return")
        if nx_first30 is None or not np.isfinite(nx_first30):
            raise ValueError("Next session 30m observation missing")
        # The first standard 15m bar begins at the exchange opening auction
        # price. For the research-only 10:00 proxy use the observed first30
        # growth relative to 09:45 bar open, NOT the daily close.
        if nxt not in minute.index or nxt not in price_10.index:
            raise ValueError("Missing verified next session minute 10:00 price")
        source_first_open=float(minute.loc[nxt,"first_open"])
        expected_return=float(price_10.loc[nxt]/source_first_open-1)
        if abs(expected_return-float(nx_first30))>0.00002:
            raise ValueError("Actual 10:00 close differs from archived next-day return")
        nx_at10=float(price_10.loc[nxt])*float(p.loc[nxt,"adjustment_factor"])
        future5=float(outlook.loc[d])
        rd=float(day_return.loc[d])
        rt=float(trend20.loc[d])
        if not np.isfinite(rt) or not np.isfinite(rd):
            raise ValueError("Insufficient 20-session warmup")
        record={
            "signal_date":event["signal_date"], "period":event["period"],
            "exchange_session_idx":i,
            "prior20_return_excluding_signal":round(rt,6),
            "prior20_regime":bin_trend(rt),
            "signal_day_return":round(rd,6),
            "signal_day_direction":bin_day(rd),
            "signal_day_15m_first30_return":event["first_30m_return"],
            "signal_day_last30_return":event["last_30m_return"],
            "next_session_first30_return":round(float(nx_first30),6),
            "next_session_first30_direction":NEXT_LABELS[0] if nx_first30<0 else NEXT_LABELS[1],
            "next_session_date":str(nxt.date()),
            "future5_close_close":round(future5,6),
            "future5_next_open_to_t5_close":round(close_t5/nx_open-1,6),
            "future5_next_10am_to_t5_close_proxy":round(close_t5/nx_at10-1,6),
            "next_open_vs_first_standard_15m_open_pct":round(nx_open/
                  (source_first_open*float(p.loc[nxt,"adjustment_factor"]))-1,6),
            "future5_min_low_vs_next_open":round(
                float(p.adj_low.iloc[i+1:i+6].min())/nx_open-1,6),
            "same_day_afternoon_volume_share":event["afternoon_vol_share"],
        }
        # Original assignment and chronological result must match.
        if known[event["signal_date"]]!=event["period"]:
            raise ValueError("Period metadata altered")
        rows.append(record)
    grouped={}
    for period in PERIODS:
        pp=[r for r in rows if r["period"]==period]
        grouped[period]={
            "total":summarize(pp),
            "nonoverlap":summarize(nonoverlap(pp)),
            "next_open_proxy":summarize(pp,"future5_next_open_to_t5_close"),
            "next_10am_proxy":summarize(pp,"future5_next_10am_to_t5_close_proxy"),
            "worst_session_low_from_next_open":summarize(pp,"future5_min_low_vs_next_open"),
            "trend_bins":{b:summarize([r for r in pp if r["prior20_regime"]==b])
                          for b in TREND_LABELS},
            "day_bins":{b:summarize([r for r in pp if r["signal_day_direction"]==b])
                        for b in DAY_LABELS},
            # This is a POST signal classification and only compared with a
            # post-10:00 proxy; never pretend it was known at signal-day close.
            "next30_bins_post_10am_only":{
                b:summarize([r for r in pp if r["next_session_first30_direction"]==b],
                            "future5_next_10am_to_t5_close_proxy")
                for b in NEXT_LABELS},
        }
    # Run-level conditional caution: no subgroup can have enough independent
    # 2026 test events for an established edge given only six total events.
    return {
        "version":VERSION,"symbol":TARGET, "start":str(START),"as_of":str(END),
        "status":"DESCRIPTIVE_SMALL_SAMPLE_NO_PROMOTABLE_EDGE",
        "source_events":len(rows),"coverage":{
            "minute_verified_days":baseline["minute_valid_days_aligned"],
            "minute_coverage":baseline["minute_daily_coverage"],
            "live_archive_rechecked_days":len(minute)},
        "frozen_event_regime_thresholds":{
            "prior20_completed_session_return":"<=-5%, >=+5%, otherwise flat",
            "same_session_adjusted_close_to_close":"<=-2%, >=+2%, otherwise neutral",
            "next_session_first30":"<0 vs >=0 observed at 10:00; NOT a signal-day input"},
        "split":grouped,"events":rows,
        "methodology":[
            "All 31 signal dates come from archived frozen daily volatility_expansion",
            "Regime uses closes T-21 through T-1; same-day direction known at T close",
            "Next day 10:00 grouping only with actual observed 10:00 minute bar-end price; price-to-T+5 close is NOT an executable trade",
            "Three years have already been inspected; 2025/2026 are retrospective checks, NOT untouched OOS",
            "Trading fees/slippage/A-share T+1/limit up-down not simulated",
            "Time-overlapping labels de-correlated descriptively via greedy 5-session spacing",
            "Subgroups with n<8 are illustrative only; multiple comparisons can create false patterns",
        ],
        "authorization":"RESEARCH_ONLY_NO_TRADING"}
    

def fmt(x):
    return "—" if x is None else f"{x:+.2%}"


def write(result,out):
    out.mkdir(parents=True,exist_ok=True)
    (out/"statistics.json").write_text(json.dumps(
        result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    lines=[
        "# 亚宝药业：波动突然扩大之后，是机会还是风险？",
        "",
        "**研究日期：2023-10-16—2026-10-16；日线与15分钟经过原始资料交叉核验。**",
        "**结论边界：这是31次历史事件的探索统计，不是可执行交易信号。**",
        "",
        "## 整体结果：原口径、次日开盘、次日10:00",
        "",
        "| 期间 | 事件数 | 日线信号收盘→五日后 | 次日开盘→五日后 | 次日10:00→五日后 | 五日内较次日开盘最深盘中跌幅均值 | 去除最佳样本后原口径均值 | 不重叠样本数 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|"
    ]
    for p in PERIODS:
        x=result["split"][p]
        lines.append(
            f"| {p} | {x['total']['n']} | {fmt(x['total']['mean'])} | "
            f"{fmt(x['next_open_proxy']['mean'])} | {fmt(x['next_10am_proxy']['mean'])} | "
            f"{fmt(x['worst_session_low_from_next_open']['mean'])} | "
            f"{fmt(x['total']['leave_best_out_mean'])} | {x['nonoverlap']['n']} |")
    for grouping,heading in (
        ("trend_bins","信号前20个交易日的方向（不含信号当天）"),
        ("day_bins","信号当天是上涨还是下跌"),
        ("next30_bins_post_10am_only","第二天早盘30分钟强弱（10:00后才可知道）")):
        lines.extend(["",f"## {heading}","","| 类别 | 2023末—2024 样本/均值 | 2025 样本/均值 | 2026 样本/均值 |",
                      "|---|---|---|---|"])
        keys=list(result["split"]["discovery"][grouping])
        for key in keys:
            v=[]
            for p in PERIODS:
                cell=result["split"][p][grouping][key]
                v.append(f"{cell['n']}次 / {fmt(cell['mean'])}")
            lines.append(f"| {key} | "+" | ".join(v)+" |")
    lines.extend([
        "",
        "## 如何理解",
        "- 原信号在当天收盘后才确认；次日开盘和10:00是价格观察代理，不是可成交回测。",
        "- 趋势分组的阈值是在本轮结果计算之前固定，2025/2026已经被研究过，不算真正未见样本。",
        "- 2026年总共只有6次，即使每组均值很高也不足以推广。",
        "- 表中五日内最低价是相对于次日开盘观察到的**最大不利波动代理**，不代表真实仓位亏损或必然先亏后赚。",
        "- 分组统计允许事件的未来5日重叠；独立事件数量请看不重叠样本。",
        "",
        "## 全部风险说明",*[f"- {s}" for s in result["methodology"]]
    ])
    (out/"report.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    sha={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
         for p in out.iterdir() if p.is_file()}
    (out/"manifest.json").write_text(json.dumps({
        "version":VERSION,"symbol":TARGET,"as_of":str(END),
        "output_sha256":sha},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--daily-root",type=Path,required=True)
    parser.add_argument("--15m-statistics",type=Path,required=True)
    parser.add_argument("--minutes-root",type=Path,required=True)
    parser.add_argument("--output-dir",type=Path,required=True)
    args=parser.parse_args()
    prior=json.loads(args.__dict__["15m_statistics"].read_text(encoding="utf-8"))
    daily, meta=read_inputs(args.daily_root,TARGET,END)
    minutes, parts=read_minute_partitions(args.minutes_root)
    if parts!=37:
        raise SystemExit(f"Original 37 minute partitions missing: found {parts}")
    out=analyze(daily,prior,minutes)
    out["source_provenance"]={
        "prior_minute_analysis_sha256":hashlib.sha256(
            args.__dict__["15m_statistics"].read_bytes()).hexdigest(),
        "daily_source_partitions":meta["source_price_partitions"],
        "daily_factor_partitions":meta["source_factor_partitions"],
        "15m_verified_partitions":parts}
    write(out,args.output_dir)
    print(json.dumps({"status":out["status"],
        "events":out["source_events"],
        "year_counts":{p:out["split"][p]["total"]["n"] for p in PERIODS},
        "tradeable":False}))


if __name__=="__main__":
    main()
