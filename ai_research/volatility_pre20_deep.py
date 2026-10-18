"""Deep audit of pre20 5–10% after the existing frozen volatility-expansion signal.

Inputs: exact archived 2026-10-16 five-stock ALL-DAYS record packet and
independently validated daily Gold OHLCV + same-date adjustment factors.
All features are known by completed T close. T+1..T+5 lows are LABELS only,
never predictive features. This is exploratory, not untouched OOS or trading.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from ai_research.kline_three_year import END, START, indicators, read_inputs
from ai_research.five_stock_pre_move_kline import STOCKS, PERIODS

VERSION="FIVE_STOCK_VOL_PRE20_BIN_DEEP_V1"
BINS=("<0%","0-5%","5-10%","10-20%",">=20%")
TARGET_BIN="5-10%"
EXPECTED_ANCHORS=133
EXPECTED_TARGET=17


def bin_for(x):
    if not np.isfinite(x):
        raise ValueError("Missing pre20 return")
    return BINS[0] if x<0 else BINS[1] if x<.05 else (
        BINS[2] if x<.10 else BINS[3] if x<.20 else BINS[4])


def raw_rows(source):
    if (source.get("version")!="FIVE_STOCK_PRE_MOVE_KLINE_V1" or
        source.get("as_of")!=str(END) or
        source.get("authorization")!="RESEARCH_ONLY_NO_TRADING" or
        set(source.get("stocks",{}))!=set(STOCKS)):
        raise ValueError("Frozen five-stock source not recognized")
    rows=[]
    for original in source["rows"]:
        if original["horizon"]!=5:
            continue
        f=original["features"]
        pre20=float(f["prior20_return"])
        rows.append({
            "stock":original["symbol"],
            "day":original["day"],
            "period":original["period"],
            "idx":int(original["index"]),
            "group":bin_for(pre20),
            "prior20":pre20,
            "frozen_expansion":int(f["frozen_vol_expansion"]),
            "r5_source":float(original["future_return"]),
        })
    if len(rows)<3000 or set(x["stock"] for x in rows)!=set(STOCKS):
        raise ValueError("Archived ALL-DAYS sample is incomplete")
    keys=[(r["stock"],r["day"]) for r in rows]
    if len(keys)!=len(set(keys)):
        raise ValueError("Duplicated archived stock/session anchors")
    frozen=[r for r in rows if r["frozen_expansion"]==1]
    if len(frozen)!=EXPECTED_ANCHORS or sum(
        x["group"]==TARGET_BIN for x in frozen)!=EXPECTED_TARGET:
        raise ValueError("Archived frozen events changed (expected 133/17)")
    return rows


def join_future_gold(raw, root):
    by_stock=defaultdict(list)
    for row in raw:
        by_stock[row["stock"]].append(row)
    records=[]
    qa={}
    for stock in STOCKS:
        frame,meta=read_inputs(root,stock,END)
        p,marks=indicators(frame)
        ix=p.index
        indices={str(d.date()):i for i,d in enumerate(ix)}
        close=p.adj_close
        adj_low=p.adj_low
        adj_high=p.adj_high
        adj_open=p.adj_open
        mismatch=0
        for row in by_stock[stock]:
            d=row["day"]
            i=indices.get(d)
            if i is None or i!=row["idx"] or i+5>=len(ix):
                raise ValueError(f"{stock}/{d}: wrong trading-calendar index")
            if close.iloc[i:i+6].isna().any() or (
                adj_low.iloc[i+1:i+6].isna().any()) or (
                adj_high.iloc[i+1:i+6].isna().any()):
                raise ValueError(f"{stock}/{d}: future-five Gold OHLC missing")
            true_r5=float(close.iloc[i+5]/close.iloc[i]-1)
            if abs(true_r5-row["r5_source"])>5e-6:
                raise ValueError(f"{stock}/{d}: source five-day return differs from Gold")
            is_exp=int(bool(marks["volatility_expansion"].iloc[i]))
            if is_exp!=row["frozen_expansion"]:
                raise ValueError(f"{stock}/{d}: source frozen signal differs from Gold")
            pre20=float(close.iloc[i]/close.iloc[i-20]-1)
            if abs(pre20-row["prior20"])>5e-6:
                raise ValueError(f"{stock}/{d}: source pre20 differs from Gold")
            next_open=float(adj_open.iloc[i+1])
            if not np.isfinite(next_open) or next_open<=0:
                raise ValueError(f"{stock}/{d}: next opening price invalid")
            future_lows=adj_low.iloc[i+1:i+6]
            future_highs=adj_high.iloc[i+1:i+6]
            raw_mae=float(future_lows.min()/close.iloc[i]-1)
            raw_mae_open=float(future_lows.min()/next_open-1)
            t5_from_nextopen=float(close.iloc[i+5]/next_open-1)
            records.append({
                **row,
                "r5":true_r5,
                "r5_next_open":t5_from_nextopen,
                "five_day_low_from_signal_close":raw_mae,
                "five_day_low_from_next_open":raw_mae_open,
                "mae_from_next_open":max(0.0,-raw_mae_open),
                "mfe_from_next_open":max(0.0,float(future_highs.max()/next_open-1)),
                "next_open_gap":float(next_open/close.iloc[i]-1),
                "within5_down5_from_signal_close":raw_mae<=-.05,
                "within5_down5_from_next_open":raw_mae_open<=-.05,
                "close_day5_down5":true_r5<=-.05,
            })
        qa[stock]={"source_records":len(by_stock[stock]),
                   "source_gold_mismatch":mismatch,
                   "invalid_source_rows":meta["invalid_source_rows"],
                   "gold_partitions_prices":meta["source_price_partitions"]}
    return pd.DataFrame(records),qa


def thinning(frame):
    """Greedy by stock across ALL expansion events, before restricting buckets."""
    picked=[]
    data=frame.loc[frame.frozen_expansion.eq(1)].sort_values(["stock","idx"])
    for stock,grp in data.groupby("stock",sort=False):
        last=-10**7
        for _,r in grp.iterrows():
            if r.idx-last>5:
                picked.append(r);last=r.idx
    if not picked:
        raise ValueError("No independent anchors")
    return pd.DataFrame(picked)


def summary(df):
    n=len(df)
    if n==0:
        return {"n":0}
    returns=df["r5"].to_numpy()
    out={
        "n":n,
        "mean5":round(float(returns.mean()),7),
        "median5":round(float(np.median(returns)),7),
        "up_count":int(np.sum(returns>0)),
        "up_rate":round(float(np.mean(returns>0)),6),
        "close_day5_down5_count":int(df.close_day5_down5.sum()),
        "close_day5_down5_rate":round(float(df.close_day5_down5.mean()),6),
        "intrawindow_low_from_next_open_down5_count":int(df.within5_down5_from_next_open.sum()),
        "intrawindow_low_from_next_open_down5_rate":round(float(df.within5_down5_from_next_open.mean()),6),
        "intrawindow_low_from_signal_close_down5_count":int(df.within5_down5_from_signal_close.sum()),
        "intrawindow_low_from_signal_close_down5_rate":round(float(df.within5_down5_from_signal_close.mean()),6),
        "next_open_to_day5_mean":round(float(df.r5_next_open.mean()),7),
        "mean_mae_next_open":round(float(df.mae_from_next_open.mean()),7),
        "median_mae_next_open":round(float(df.mae_from_next_open.median()),7),
        "worst_mae_next_open":round(float(df.mae_from_next_open.max()),7),
        "worst5":round(float(returns.min()),7),
        "best5":round(float(returns.max()),7),
        "mean_without_best":round(float((returns.sum()-returns.max())/(n-1)),7)
            if n>1 else None,
    }
    return out


def grouped(df, thinned):
    answer={}
    for period in ("all",)+PERIODS:
        selected=df if period=="all" else df.loc[df.period.eq(period)]
        independent=thinned if period=="all" else thinned.loc[thinned.period.eq(period)]
        answer[period]={}
        for b in BINS:
            condition=selected.group.eq(b)
            independent_condition=independent.group.eq(b)
            answer[period][b]={
                "expansion":summary(selected.loc[condition & selected.frozen_expansion.eq(1)]),
                "non_expansion_same_pre20":summary(selected.loc[condition & selected.frozen_expansion.eq(0)]),
                "expansion_deoverlapped":summary(independent.loc[independent_condition]),
            }
    return answer


def matched_sampling(frame,repeats=10000,seed=260019):
    """Illustrative matched-stock/year ordinary-day Monte Carlo; NOT a p-value."""
    selected=frame[(frame.group==TARGET_BIN)&(frame.frozen_expansion==1)]
    ordinary=frame[(frame.group==TARGET_BIN)&(frame.frozen_expansion==0)]
    rng=np.random.default_rng(seed)
    buckets=[]
    for (stock,period),grp in selected.groupby(["stock","period"]):
        controls=ordinary[(ordinary.stock==stock)&(ordinary.period==period)]
        if len(controls)<len(grp):
            raise ValueError("Not enough stock/year matched non-expansion dates")
        buckets.append((controls["r5"].to_numpy(),len(grp)))
    if sum(n for _,n in buckets)!=EXPECTED_TARGET:
        raise ValueError("Expected all 17 matched observations")
    results=np.empty(repeats)
    tail=np.empty(repeats,dtype=int)
    for j in range(repeats):
        choice=np.concatenate([rng.choice(x,size=n,replace=False) for x,n in buckets])
        results[j]=choice.mean()
        tail[j]=int(np.sum(choice<=-.05))
    observed=float(selected.r5.mean())
    return {
        "method":"stock/year matched ordinary 5-10% days; dependent windows + exploratory chosen bin; NOT inferential significance",
        "draws":repeats,"seed":seed,
        "matched_ordinary_mean":round(float(results.mean()),7),
        "observed_mean":round(observed,7),
        "observed_minus_matched_mean":round(observed-float(results.mean()),7),
        "matched_fraction_at_least_observed_mean":round(float(np.mean(results>=observed)),6),
        "matched_fraction_zero_5pct_day5_declines":round(float(np.mean(tail==0)),6),
    }


def compose(root,source,manifest):
    if (manifest.get("as_of_date","")<str(END) or
        manifest.get("validation",{}).get("primary_keys_unique") is not True or
        manifest.get("validation",{}).get("ohlc_valid") is not True):
        raise ValueError("Gold input manifest invalid")
    source_rows=raw_rows(source)
    full,qa=join_future_gold(source_rows,root)
    thinned=thinning(full)
    total=full[full.frozen_expansion.eq(1)]
    if len(total)!=EXPECTED_ANCHORS or len(total[total.group==TARGET_BIN])!=EXPECTED_TARGET:
        raise ValueError("Frozen five-stock event totals changed")
    result={
        "version":VERSION,"start":str(START),"as_of":str(END),
        "signal":"unmodified daily volatility_expansion in kline_three_year.indicators()",
        "bins":list(BINS),"focus":TARGET_BIN,
        "coverage":{
            "five_stock_complete_anchor_rows":len(full),
            "expansion_events":len(total),
            "focus_5_to_10_events":EXPECTED_TARGET,
            "expansion_thinned":len(thinned),
            "focus_5_to_10_thinned":len(thinned[thinned.group==TARGET_BIN]),
            "gold_source_verification":qa},
        "grouped":grouped(full,thinned),
        "matched_control":matched_sampling(full),
        "focus_by_stock":{
            s:summary(total[(total.stock==s)&(total.group==TARGET_BIN)])
            for s in STOCKS},
        "focus_individual_events":full[(full.frozen_expansion==1)&
            (full.group==TARGET_BIN)].sort_values(["period","day","stock"]).round(7).to_dict("records"),
        "interpretation_limits":[
            "2023-2026 data and 5-10% focus bin already seen: selection bias / no untouched holdout",
            "Overlapping five-session outcomes, common sector/market shocks and only five handpicked stocks",
            "A high/low over T+1..T+5 is retrospective, not necessarily a fillable path",
            "5-day close-to-close returns are NOT trading PnL; orders after T close at earliest next session",
            "Next-open proxy ignores limits, halts, partial fills, slippage, fees, A-share T+1",
            "Risk minimum is point-in-time future-adjusted DAILY low; intraday timing/order unavailable",
            "Matched Monte Carlo uses dependent historic sessions and must NOT be sold as p-value",
            "No predictive rules or thresholds promoted or R1 production strategy changed",
        ],
        "authorization":"RESEARCH_ONLY_NO_TRADING",
    }
    return result,full,thinned


def pct(x):
    return "—" if x is None else f"{x:+.2%}"


def make_report(x):
    g=x["grouped"]
    lines=[
        "# 五股：波动扩大前20日涨幅 5%–10% 是否真的更安全？",
        "",
        "**2023-10-16 至 2026-10-16。原形态、前20日涨幅分界都没有调整。仅历史研究，不用于自动交易。**",
        "",
        "## 总体：按20日涨幅分档，事件与同区间普通日对照",
        "",
        "| 前20日 | 波动扩大 n | 第五日平均 | 第五日跌≥5% | 次日开盘后五天内最低价跌≥5% | 同区间非信号 n / 第五日平均 | 去重信号 n |",
        "|---|---:|---:|---:|---:|---|---:|"
    ]
    for bin_name in BINS:
        y=g["all"][bin_name];a=y["expansion"];b=y["non_expansion_same_pre20"];c=y["expansion_deoverlapped"]
        lines.append(f"| {bin_name} | {a['n']} | {pct(a.get('mean5'))} | "
            f"{a.get('close_day5_down5_rate',0):.1%} | "
            f"{a.get('intrawindow_low_from_next_open_down5_rate',0):.1%} | "
            f"{b['n']} / {pct(b.get('mean5'))} | {c['n']} |")
    lines+=["","## 5%–10%单独按年份",
        "| 年份 | 信号n | 平均5日 | 中位5日 | 上涨率 | 五日内从次日开盘最大下探≥5%的比例 | 非信号n / 平均 |",
        "|---|---:|---:|---:|---:|---:|---|"]
    for period in PERIODS:
        v=g[period][TARGET_BIN];a=v["expansion"];b=v["non_expansion_same_pre20"]
        lines.append(f"| {period} | {a['n']} | {pct(a.get('mean5'))} | {pct(a.get('median5'))} | "
            f"{a.get('up_rate',0):.1%} | "
            f"{a.get('intrawindow_low_from_next_open_down5_rate',0):.1%} | "
            f"{b['n']} / {pct(b.get('mean5'))} |")
    focus=g["all"][TARGET_BIN]["expansion"]
    lines+=["","## 用实际每日最低价检验风险",
        f"- 第五日收盘跌≥5%：{focus['close_day5_down5_count']}/{focus['n']}次。",
        f"- 从**次日开盘**起，五天里任一日最低价跌≥5%："
        f"{focus['intrawindow_low_from_next_open_down5_count']}/{focus['n']}次。",
        f"- 从信号当日收盘起，五天里任一日最低价跌≥5%："
        f"{focus['intrawindow_low_from_signal_close_down5_count']}/{focus['n']}次。",
        f"- 次日开盘到第五日收盘平均：{pct(focus['next_open_to_day5_mean'])}；"
        f"均值去掉单次最佳上涨后：{pct(focus.get('mean_without_best'))}。",
        "",
        "## 分股票",
        "| 股票 | 样本 | 平均五日 | 上涨率 | 5日内次日开盘最大下探≥5% |",
        "|---|---:|---:|---:|---:|"]
    for s,val in x["focus_by_stock"].items():
        lines.append(f"| {STOCKS[s]} | {val['n']} | {pct(val.get('mean5'))} | "
            f"{val.get('up_rate',0):.1%} | "
            f"{val.get('intrawindow_low_from_next_open_down5_rate',0):.1%} |")
    mc=x["matched_control"]
    lines+=["","## 匹配普通日的随机模拟（探索性，绝非统计显著性证明）",
        f"- 同股票、同年份、同样前20日上涨5%–10%、但没有波动扩大的普通日：模拟{mc['draws']}组。",
        f"- 匹配普通日的平均五日收益约 {pct(mc['matched_ordinary_mean'])}，"
        f"触发信号的17次平均 {pct(mc['observed_mean'])}。",
        f"- 随机样本平均收益高于或等于观测值的比例：{mc['matched_fraction_at_least_observed_mean']:.2%}。",
        "- 这是事后选择最亮眼分档之后做的检验，且行情高度相关，不得解释为可靠p值或交易胜率。",
        "",
        "## 局限与需要后续验证",*[f"- {lim}" for lim in x["interpretation_limits"]]]
    return "\n".join(lines)+"\n"


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--input",type=Path,required=True)
    p.add_argument("--gold-root",type=Path,required=True)
    p.add_argument("--gold-manifest",type=Path,required=True)
    p.add_argument("--output-dir",type=Path,required=True)
    args=p.parse_args()
    source_bytes=args.input.read_bytes()
    manifest_bytes=args.gold_manifest.read_bytes()
    result,whole,thinned=compose(args.gold_root,json.loads(source_bytes),json.loads(manifest_bytes))
    result["source_sha256"]=hashlib.sha256(source_bytes).hexdigest()
    result["gold_manifest_sha256"]=hashlib.sha256(manifest_bytes).hexdigest()
    args.output_dir.mkdir(parents=True,exist_ok=True)
    (args.output_dir/"statistics.json").write_text(
        json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    whole.loc[whole.frozen_expansion.eq(1)].sort_values(
        ["stock","day"]).to_csv(args.output_dir/"all_expansion_events.csv",index=False)
    thinned.sort_values(["stock","day"]).to_csv(
        args.output_dir/"nonoverlap_expansion_events.csv",index=False)
    (args.output_dir/"report.md").write_text(make_report(result),encoding="utf-8")
    checks={f.name:hashlib.sha256(f.read_bytes()).hexdigest()
            for f in args.output_dir.iterdir() if f.is_file()}
    (args.output_dir/"manifest.json").write_text(json.dumps({
        "version":VERSION,"as_of":str(END),"files_sha256":checks,
        "source_sha256":result["source_sha256"],
        "gold_manifest_sha256":result["gold_manifest_sha256"]},
        ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({
        "status":"DEEP_STUDY_RESEARCH_ONLY",
        "events":result["coverage"]["expansion_events"],
        "focus_events":result["coverage"]["focus_5_to_10_events"],
        "focus_5pct_intrawindow_down_count":result["grouped"]["all"][TARGET_BIN]["expansion"][
            "intrawindow_low_from_next_open_down5_count"],
        "tradeable":False},ensure_ascii=False))


if __name__=="__main__":
    main()
