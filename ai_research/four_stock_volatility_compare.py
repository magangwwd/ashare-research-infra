"""Four-stock replication of the FROZEN 600351 volatility-expansion rule.

The rule is imported without changes from kline_three_year.indicators.
Historical period and bins are identical to the 600351 regime review.
No threshold optimization, minute bars, trading, or forward-test claims.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ai_research.kline_three_year import START, END, indicators, read_inputs
from ai_research.kline_volatility_regime import (
    PERIODS, TREND_LABELS, DAY_LABELS, bin_trend, bin_day, summarize, nonoverlap,
)

VERSION="KLINE_FOUR_STOCK_FROZEN_VOL_REPLICATION_V1"
SYMBOLS={
    "002107.SZ": "沃华医药",
    "600641.SH": "先导基电",
    "002568.SZ": "百润股份",
    "002296.SZ": "辉煌科技",
}
REFERENCE="600351.SH"


def period_of(day):
    return ("discovery" if day <= "2024-12-31" else
            "validation" if day <= "2025-12-31" else "test")


def prepared_one(daily, symbol):
    p, marks=indicators(daily)
    adjusted=p.adj_close
    sessions=list(p.index)
    prior20=adjusted.shift(1)/adjusted.shift(21)-1
    dayret=adjusted/adjusted.shift(1)-1
    h5=adjusted.shift(-5)/adjusted-1
    samples=[]
    exclusions={"missing_future_or_warmup":0, "untradeable_next_open_or_low":0}
    all_days=(p.index.date>=START)&(p.index.date<=END)
    close_days=int(p.loc[all_days, "close"].notna().sum())
    if close_days<500:
        raise ValueError(f"{symbol}: only {close_days} complete daily Gold closes")
    for idx in np.flatnonzero((marks["volatility_expansion"]&all_days).to_numpy()):
        dt=sessions[idx]
        if idx+5>=len(sessions) or not all(np.isfinite(v) for v in
                 [prior20.iloc[idx],dayret.iloc[idx],h5.iloc[idx]]):
            exclusions["missing_future_or_warmup"]+=1
            continue
        nextopen=p.adj_open.iloc[idx+1]
        low=p.adj_low.iloc[idx+1:idx+6]
        if (not np.isfinite(nextopen) or nextopen<=0 or
            low.isna().any() or len(low)!=5 or not np.isfinite(low).all()):
            exclusions["untradeable_next_open_or_low"]+=1
            continue
        v=float(h5.iloc[idx])
        trend=float(prior20.iloc[idx])
        day=float(dayret.iloc[idx])
        next_open_ret=float(adjusted.iloc[idx+5]/nextopen-1)
        min_low_ret=float(low.min()/nextopen-1)
        samples.append({
            "symbol":symbol,"signal_date":str(dt.date()),
            "period":period_of(str(dt.date())),
            "exchange_session_idx":int(idx),
            "prior20_return_excluding_signal":round(trend,6),
            "prior20_regime":bin_trend(trend),
            "signal_day_return":round(day,6),
            "signal_day_direction":bin_day(day),
            "future5_close_close":round(v,6),
            "future5_next_open_to_t5_close":round(next_open_ret,6),
            "future5_min_low_vs_next_open":round(min_low_ret,6),
        })
    grouped={}
    for period in PERIODS:
        sub=[r for r in samples if r["period"]==period]
        period_mask=(p.index.date>=START)&(p.index.date<=END)&(
            (p.index.strftime("%Y").astype(int)<2025) if period=="discovery" else
            (p.index.strftime("%Y")=="2025") if period=="validation" else
            (p.index.strftime("%Y")=="2026"))
        # Fair baseline: exclude last five sessions of each period, and
        # require availability of the T+5 label in that period.
        dates=pd.Series(p.index, index=p.index)
        future_dates=dates.shift(-5)
        same_period=(future_dates.notna() & future_dates.apply(
            lambda t: (period_of(str(t.date()))==period)
            if isinstance(t,pd.Timestamp) else False))
        baseline=h5.loc[period_mask & same_period.to_numpy() & h5.notna().to_numpy()]
        grouped[period]={
            "signals":summarize(sub),
            "nonoverlap_signals":summarize(nonoverlap(sub)),
            "next_open":summarize(sub,"future5_next_open_to_t5_close"),
            "five_session_min_low_from_next_open":summarize(sub,"future5_min_low_vs_next_open"),
            "baseline_ordinary_days":summarize(
                [{"future5_close_close":v} for v in baseline.to_numpy()]),
            "trend_bins":{k:summarize([r for r in sub if r["prior20_regime"]==k])
                          for k in TREND_LABELS},
            "day_bins":{k:summarize([r for r in sub if r["signal_day_direction"]==k])
                        for k in DAY_LABELS},
            "overlap_warning":len(nonoverlap(sub))<len(sub),
        }
    result2025=grouped["validation"]["signals"]
    control2025=grouped["validation"]["baseline_ordinary_days"]
    return {
        "symbol":symbol,"name":SYMBOLS[symbol],
        "valid_daily_gold_days":close_days,"excluded":exclusions,
        "event_count":len(samples),
        "split":grouped,"events":samples,
        "2025_negative_mean":result2025["n"]>0 and result2025["mean"]<0,
        "2025_negative_with_min5":result2025["n"]>=5 and result2025["mean"]<0,
        "2025_signal_minus_baseline":round(
            result2025["mean"]-control2025["mean"],6)
            if result2025["mean"] is not None and control2025["mean"] is not None else None,
    }


def compute(root, manifest):
    if manifest["as_of_date"] < str(END):
        raise ValueError("Gold not current through frozen 2026-10-16")
    if (manifest.get("validation",{}).get("primary_keys_unique") is not True or
        manifest.get("validation",{}).get("ohlc_valid") is not True):
        raise ValueError("Gold quality flags failed")
    by_stock={}
    for symbol in SYMBOLS:
        daily, meta=read_inputs(root,symbol,END)
        item=prepared_one(daily,symbol)
        item["source_validity"]={**meta,"as_of":str(END)}
        by_stock[symbol]=item
    matched=[s for s,v in by_stock.items()
             if v["2025_negative_with_min5"]]
    negatives=[s for s,v in by_stock.items() if v["2025_negative_mean"]]
    return {
        "version":VERSION,"period_start":str(START),"as_of":str(END),
        "symbols":by_stock,"symbol_count":4,
        "frozen_rule":"kline_three_year.indicators.volatility_expansion (std(last5 returns)>1.6*std(prior20 returns) AND threshold newly crossed)",
        "rule_source":"ai_research/kline_three_year.py",
        "trend_bins":"prior20 completed sessions (excluding signal) <=-5%; >=+5%; else flat",
        "day_bins":"daily close-to-close <=-2%; >=+2%; otherwise neutral",
        "question_2025":{
            "all_four_with_valid_samples":all(v["split"]["validation"]["signals"]["n"]>0 for v in by_stock.values()),
            "negative_2025_stock_count":len(negatives),
            "negative_2025_symbols":negatives,
            "negative_2025_at_least_five_events_count":len(matched),
            "negative_2025_at_least_five_events_symbols":matched,
            "note":"If sample sizes are too small, signs are descriptive; 4 hand-picked symbols cannot establish a market-wide 2025 effect."
        },
        "source_manifest_sha256":None,
        "risk_flags":[
            "Original frozen 1.6x volatility rule, not retrained; no cherry-picked thresholds",
            "Data limited to selected 5 stocks; not representative of entire A-share market",
            "2023-2026 already inspected; validation/test are chronological retrospective, not untouched OOS",
            "Future 5-session close-close values are labels, not executable entry/exit returns",
            "Next open is a proxy; no actual fill, T+1, price limit, slippage or transaction costs",
            "Clustered adjacent events share outcomes; nonoverlap metric is a sensitivity check",
            "Events with missing outcomes or valid next-day price omitted and counted",
            "No 15-minute data assumed for the four additional stocks in this phase"
        ],
        "authorization":"RESEARCH_ONLY_NO_TRADING",
    }


def as_pct(v):
    return "—" if v is None else f"{v:+.2%}"


def render(result):
    lines=[
        "# 固定五股交叉验证：另外四只股票有没有2025年波动扩大风险？",
        "",
        "**研究截止：2026-10-16。完全复用亚宝药业已冻结的日线波动扩张信号。**",
        "**不使用财务、新闻或AI评分，也未重调形态阈值。**",
        "",
        "## 四只股票逐年对比",
        "",
        "| 股票 | 2023末—2024：次数/5日均值 | 2025：次数/5日均值 | 2026：次数/5日均值 | 2025较普通交易日 |",
        "|---|---|---|---|---|"
    ]
    for s,v in result["symbols"].items():
        cols=[]
        for period in PERIODS:
            a=v["split"][period]["signals"]
            cols.append(f"{a['n']}次 / {as_pct(a['mean'])}")
        lines.append(f"| {v['name']} ({s}) | "+" | ".join(cols)+
                     f" | {as_pct(v['2025_signal_minus_baseline'])} |")
    n=result["question_2025"]
    lines.extend([
        "",
        f"**2025年信号后5日均值为负：{n['negative_2025_stock_count']}/4只；"
        f"且2025年不少于5次事件者：{n['negative_2025_at_least_five_events_count']}/4只。**",
        "",
        "## 两项独立风险检查",
        "",
        "| 股票 | 2025年次日开盘至第5日均值 | 2025年次日开盘后5日内最深下探均值 | 2025年互不重叠事件 | 2025年最差单次 |",
        "|---|---:|---:|---:|---:|"
    ])
    for s,v in result["symbols"].items():
        sp=v["split"]["validation"]
        lines.append(f"| {v['name']} | {as_pct(sp['next_open']['mean'])} | "
                     f"{as_pct(sp['five_session_min_low_from_next_open']['mean'])} | "
                     f"{sp['nonoverlap_signals']['n']} | {as_pct(sp['signals']['min'])} |")
    lines.extend([
        "",
        "## 2025年不同前期趋势的样本",
        "",
        "| 股票 | 之前20日下跌>=5% | 之前20日横盘 | 之前20日上涨>=5% |",
        "|---|---|---|---|"
    ])
    for s,v in result["symbols"].items():
        bins=v["split"]["validation"]["trend_bins"]
        cols=[f"{bins[k]['n']}次 / {as_pct(bins[k]['mean'])}" for k in TREND_LABELS]
        lines.append(f"| {v['name']} | "+" | ".join(cols)+" |")
    lines+=["","## 必须读的边界",*[f"- {x}" for x in result["risk_flags"]]]
    return "\n".join(lines)+"\n"


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--gold-root",type=Path,required=True)
    p.add_argument("--gold-manifest",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args()
    manifest=json.loads(a.gold_manifest.read_text())
    out=compute(a.gold_root,manifest)
    out["source_manifest_sha256"]=hashlib.sha256(a.gold_manifest.read_bytes()).hexdigest()
    a.out.mkdir(parents=True,exist_ok=True)
    (a.out/"statistics.json").write_text(
        json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (a.out/"report.md").write_text(render(out),encoding="utf-8")
    digests={q.name:hashlib.sha256(q.read_bytes()).hexdigest()
             for q in a.out.iterdir() if q.is_file()}
    (a.out/"manifest.json").write_text(json.dumps({
        "version":VERSION,"as_of":str(END),"symbols":list(SYMBOLS),
        "sha256":digests},ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({"status":"RESEARCH_DESCRIPTIVE_NONTRADE",
        "negative_2025":out["question_2025"],
        "samples_2025":{s:v["split"]["validation"]["signals"]["n"]
                        for s,v in out["symbols"].items()}}))


if __name__=="__main__":
    main()
