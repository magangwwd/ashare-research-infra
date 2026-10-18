"""Pre-move Kline comparison for the fixed five A shares, observational ONLY.

Question: As of close T, which candle/volume features distinguish an eventual
large UP move from an eventual large DOWN move in T+1 or T+1..T+5?
Events are labeled ex post; all input features stop at session T. The old
1.6x volatility expansion rule is imported unmodified as a feature, not
trained or optimized. This is not a trading backtest nor untouched holdout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ai_research.kline_three_year import START, END, indicators, read_inputs

VERSION = "FIVE_STOCK_PRE_MOVE_KLINE_V1"
STOCKS = {
    "600351.SH": "亚宝药业", "002107.SZ": "沃华医药",
    "600641.SH": "先导基电", "002568.SZ": "百润股份",
    "002296.SZ": "辉煌科技",
}
PERIODS = ("2023_2024", "2025", "2026")
HORIZONS = {1: 0.05, 5: 0.10}  # PREdeclared large movement definitions
FEATURES = {
    "prior5_return": "前5日涨跌幅",
    "prior20_return": "前20日涨跌幅",
    "prior20_vol": "前20日日收益标准差",
    "vol5_vs_prior20": "近5日波动与20日波动之比",
    "volume_day_vs_prior20": "当天成交量/此前20日中位量",
    "volume5_vs_prior20": "近5日平均量/此前20日平均量",
    "last_bar_range": "当天最高最低价振幅",
    "last_bar_body": "当天K线实体方向/振幅",
    "upper_wick": "当天上影线比例",
    "lower_wick": "当天下影线比例",
    "close_position20": "收盘在近20日价格区间的位置",
    "down_days5": "近5日收跌天数",
    "close_break20": "收盘突破前20日收盘最高",
    "frozen_vol_expansion": "既有冻结1.6倍波动扩大信号",
}
MIN_GROUP_FOR_INTERPRETATION = 8


def which_period(timestamp):
    d = str(pd.Timestamp(timestamp).date())
    return "2023_2024" if d <= "2024-12-31" else "2025" if d <= "2025-12-31" else "2026"


def features_from_gold(df):
    p, marks = indicators(df)
    c, o, h, l = (p["adj_"+k] for k in ("close", "open", "high", "low"))
    daily = c.pct_change(fill_method=None)
    priorclose = c.shift(1)
    daily_range = h - l
    spread = daily_range.where(daily_range.gt(0))
    prior_vol20 = daily.rolling(20, min_periods=20).std().shift(1)
    vol5 = daily.rolling(5, min_periods=5).std()
    vol20 = daily.rolling(20, min_periods=20).std()
    prev_vol5 = daily.shift(1).rolling(5, min_periods=5).std()
    v = p.volume_shares
    pr_v20_median = v.shift(1).rolling(20, min_periods=20).median()
    pr_v20_mean = v.shift(1).rolling(20, min_periods=20).mean()
    price_high20 = h.rolling(20, min_periods=20).max()
    price_low20 = l.rolling(20, min_periods=20).min()
    result = pd.DataFrame({
        "prior5_return": c / c.shift(5) - 1,
        "prior20_return": c / c.shift(20) - 1,
        "prior20_vol": vol20,
        "vol5_vs_prior20": vol5 / prior_vol20.replace(0,np.nan),
        "volume_day_vs_prior20": v / pr_v20_median.replace(0,np.nan),
        "volume5_vs_prior20": v.rolling(5, min_periods=5).mean() /
                               pr_v20_mean.replace(0,np.nan),
        "last_bar_range": daily_range / priorclose,
        "last_bar_body": (c-o) / spread,
        "upper_wick": (h - np.maximum(o,c))/spread,
        "lower_wick": (np.minimum(o,c)-l)/spread,
        "close_position20": (c-price_low20)/(price_high20-price_low20).replace(0,np.nan),
        "down_days5": daily.lt(0).rolling(5, min_periods=5).sum(),
        "close_break20": (c > c.shift(1).rolling(20,min_periods=20).max()).astype(float),
        "frozen_vol_expansion": marks["volatility_expansion"].astype(float),
    },index=p.index)
    # Do not turn missing candles / suspended-day returns into neutral indicators.
    valid_closes = c.rolling(21,min_periods=21).count().eq(21)
    valid_volumes = v.rolling(21,min_periods=21).count().eq(21)
    result.loc[~(valid_closes & valid_volumes),:] = np.nan
    return result, c, p


def episodes(row_indices, spacing):
    """Stable chronological first anchor after prior chosen event's horizon."""
    kept=[]
    last=-10**9
    for i in row_indices:
        if int(i)-last>spacing:
            kept.append(int(i))
            last=int(i)
    return kept


def one_stock(root, symbol):
    raw, meta = read_inputs(root, symbol, END)
    feat, c, adjusted=features_from_gold(raw)
    periods={}
    records=[]
    eligible_count=0
    for horizon, limit in HORIZONS.items():
        outcomes=(c.shift(-horizon)/c-1)
        for i in range(len(c)-horizon):
            d=c.index[i]
            if d.date()<START or d.date()>END:
                continue
            if which_period(d)!=which_period(c.index[i+horizon]):
                continue
            if feat.iloc[i].isna().any() or not np.isfinite(outcomes.iloc[i]):
                continue
            if c.iloc[i:i+horizon+1].isna().any():
                continue
            # The label is known only AFTER horizon sessions, never a feature.
            y=float(outcomes.iloc[i])
            direction="BIG_UP" if y>=limit else "BIG_DOWN" if y<=-limit else "NORMAL"
            rec={
                "symbol":symbol,"day":str(d.date()),"index":i,
                "period":which_period(d),"horizon":horizon,
                "label":direction,"future_return":round(y,7),
                "features":{key:round(float(feat.iloc[i][key]),7) for key in FEATURES},
            }
            records.append(rec)
            eligible_count+=1
    for horizon in HORIZONS:
        periods[str(horizon)]={}
        for p in PERIODS:
            rows=[r for r in records if r["horizon"]==horizon and r["period"]==p]
            sets={label:[r for r in rows if r["label"]==label]
                  for label in ("BIG_UP","BIG_DOWN","NORMAL")}
            thinned={}
            for lab in ("BIG_UP","BIG_DOWN"):
                keep=set(episodes([r["index"] for r in sets[lab]],horizon))
                thinned[lab]=[r for r in sets[lab] if r["index"] in keep]
            periods[str(horizon)][p]={
                "anchor_days":len(rows),
                "class_counts":{lab:len(group) for lab,group in sets.items()},
                "nonoverlap_counts":{lab:len(group) for lab,group in thinned.items()},
                "big_up_mean_future":_avg_future(sets["BIG_UP"]),
                "big_down_mean_future":_avg_future(sets["BIG_DOWN"]),
            }
    return records,{"name":STOCKS[symbol],"valid_eligible_anchor_horizons":eligible_count,
                    "valid_daily_closes":int(c.loc[(c.index.date>=START)&
                                                   (c.index.date<=END)].notna().sum()),
                    "source":meta,"counts":periods}


def _avg_future(rows):
    return round(float(np.mean([r["future_return"] for r in rows])),6) if rows else None


def group_difference(rows, horizon, period):
    group=[r for r in rows if r["horizon"]==horizon and r["period"]==period]
    up=[r for r in group if r["label"]=="BIG_UP"]
    down=[r for r in group if r["label"]=="BIG_DOWN"]
    base=[r for r in group if r["label"]=="NORMAL"]
    table=[]
    for f in FEATURES:
        a=np.array([r["features"][f] for r in up],dtype=float)
        b=np.array([r["features"][f] for r in down],dtype=float)
        n=np.array([r["features"][f] for r in base],dtype=float)
        table.append({
            "feature":f,"meaning":FEATURES[f],
            "up_n":len(a),"down_n":len(b),"normal_n":len(n),
            "up_median":round(float(np.median(a)),6) if len(a) else None,
            "down_median":round(float(np.median(b)),6) if len(b) else None,
            "normal_median":round(float(np.median(n)),6) if len(n) else None,
            "up_minus_down_median":round(float(np.median(a)-np.median(b)),6)
                                    if len(a) and len(b) else None,
            "interpretation_eligible":len(a)>=MIN_GROUP_FOR_INTERPRETATION and
                                      len(b)>=MIN_GROUP_FOR_INTERPRETATION,
        })
    return {
        "labels":{lab:sum(r["label"]==lab for r in group)
                  for lab in ("BIG_UP","BIG_DOWN","NORMAL")},
        "nonoverlap_labels":{
            lab:sum(len(episodes(
                [r["index"] for r in group if r["symbol"]==sym and
                 r["label"]==lab],horizon))
                for sym in STOCKS)
            for lab in ("BIG_UP","BIG_DOWN")},
        "feature_compare":table
    }


def compute(root, manifest):
    if str(manifest.get("as_of_date"))<str(END):
        raise ValueError("Validated Gold as-of is stale")
    quality=manifest.get("validation",{})
    if quality.get("primary_keys_unique") is not True or quality.get("ohlc_valid") is not True:
        raise ValueError("Gold uniqueness/OHLC validation missing")
    stocks={}
    all_records=[]
    for s in STOCKS:
        rec,summary=one_stock(root,s)
        stocks[s]=summary
        all_records.extend(rec)
    pooled={}
    for h in HORIZONS:
        pooled[str(h)]={period:group_difference(all_records,h,period)
                        for period in PERIODS}
    # Check five stock cohorts and all label metadata, never silently
    # assume a stock missing a full year has zero extraordinary events.
    for s in STOCKS:
        if stocks[s]["valid_daily_closes"]<700:
            raise ValueError(s+" Gold has <700 daily sessions")
    return {
        "version":VERSION, "as_of":str(END), "start":str(START),
        "stocks":stocks,
        "pooled":pooled,
        "label_definition":{
            "horizon_1":"tomorrow T+1 adjusted close / T adjusted close - 1; >=+5%, <=-5%",
            "horizon_5":"T+5 adjusted close / T adjusted close - 1; >=+10%, <=-10%",
            "anchor":"all Kline inputs known by close of T; event label known only after T+h",
        },
        "source_rule":"Original frozen vol expansion indicators() from kline_three_year.py is UNCHANGED.",
        "risk_flags":[
            "Five handpicked fixed stocks are NOT representative of the full A-share market",
            "The 2025/2026 blocks were already viewed historically: NOT untouched out-of-sample",
            "Multiple correlated candle features and rare tail outcomes invite false discoveries",
            "Pooled event rows are NOT independent due to stock/date clustering and future-window overlap",
            "Use nonoverlap counts and cross-ticker consistency before interpretation",
            "No 15-minute bars are included in this pre-move study",
            "Close-to-close outcome is descriptive, NOT realizable PnL (A-share T+1, limits, fees, slippage)",
            "No new trade authorization or changes to R1",
        ],
        "authorization":"RESEARCH_ONLY_NO_TRADING",
        # Individual anchors retained for audit, including the NORMAL reference group
        "rows":all_records,
    }


def perc(x):
    return "—" if x is None else f"{x:+.2%}"


def report(x):
    lines=[
        "# 五股大涨、大跌之前的K线：三年事前特征研究",
        "",
        f"**截止 {x['as_of']}；纯日线；使用日线收盘时已知数据。**",
        "次日单日涨跌>=5% / <=-5%；或未来5个交易日累计涨跌>=10% / <=-10%；两类事件分开研究。",
        "**观察到的差异不等于预测能力或可实现交易收益。**",
        "",
        "## 1. 事件数（先检查有多少样本）",
        "| 时间段 | 单日大涨 | 单日大跌 | 五日大涨 | 五日大跌 |",
        "|---|---:|---:|---:|---:|",
    ]
    for y in PERIODS:
        one=x["pooled"]["1"][y]["labels"]
        five=x["pooled"]["5"][y]["labels"]
        lines.append(f"| {y} | {one['BIG_UP']} | {one['BIG_DOWN']} | {five['BIG_UP']} | {five['BIG_DOWN']} |")
    lines+=["","## 2. 先看五日大涨/大跌之前的差别（各指标中位数）",""]
    for y in PERIODS:
        part=x["pooled"]["5"][y]
        lines.append(f"### {y}：大涨{part['labels']['BIG_UP']}次，大跌{part['labels']['BIG_DOWN']}次"
                     f"；间隔去重后分别{part['nonoverlap_labels']['BIG_UP']}、{part['nonoverlap_labels']['BIG_DOWN']}次")
        lines+=["","| 信号前已知特征 | 大涨前 | 大跌前 | 普通时期 | 大涨-大跌 |",
                 "|---|---:|---:|---:|---:|"]
        for v in part["feature_compare"]:
            f=v["feature"]
            scale=100 if f in ("prior5_return","prior20_return","prior20_vol",
                               "last_bar_range","last_bar_body","upper_wick","lower_wick",
                               "close_position20","close_break20",
                               "frozen_vol_expansion") else 1
            def fmt(z):
                if z is None:return "—"
                return f"{z*scale:.2f}"+("%" if scale==100 else "")
            lines.append(f"| {v['meaning']} | {fmt(v['up_median'])} | "
                         f"{fmt(v['down_median'])} | {fmt(v['normal_median'])} | "
                         f"{fmt(v['up_minus_down_median'])} |")
        if not all(v["interpretation_eligible"] for v in part["feature_compare"]):
            lines.append("**此期间单侧少于8次：仅供了解，不能判断方向规律。**")
    lines+=["","## 3. 各股票事件数量，避免被大股样本淹没",
             "| 股票 | 2025 单日上涨/下跌 | 2025 五日上涨/下跌 | 2026 五日上涨/下跌 |",
             "|---|---|---|---|"]
    for sym,s in x["stocks"].items():
        s1=s["counts"]["1"]["2025"]["class_counts"]
        s5=s["counts"]["5"]["2025"]["class_counts"]
        s6=s["counts"]["5"]["2026"]["class_counts"]
        lines.append(f"| {s['name']} | {s1['BIG_UP']} / {s1['BIG_DOWN']} | "
                     f"{s5['BIG_UP']} / {s5['BIG_DOWN']} | "
                     f"{s6['BIG_UP']} / {s6['BIG_DOWN']} |")
    lines+=["","## 4. 能否用来预测？",
            "- 不可以直接用于买卖。只能将跨年与跨股票重复的特征登记为候选，等待之后未见的新数据。",
            "- 需要与同股票同期普通K线比较，按时间去重、控制市场阶段影响和对强相关特征作多重比较防护。",
            "- 如果某年上/下样本不足8次，在表中只描述，不做结论。",
            "",
            "## 数据与解释边界",*[f"- {v}" for v in x["risk_flags"]]]
    return "\n".join(lines)+"\n"


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--gold-root",type=Path,required=True)
    p.add_argument("--gold-manifest",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args()
    manifest=json.loads(a.gold_manifest.read_text(encoding="utf-8"))
    result=compute(a.gold_root,manifest)
    result["source_manifest_sha256"]=hashlib.sha256(a.gold_manifest.read_bytes()).hexdigest()
    a.out.mkdir(parents=True,exist_ok=True)
    (a.out/"statistics.json").write_text(
        json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (a.out/"report.md").write_text(report(result),encoding="utf-8")
    hash1={f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in a.out.iterdir()}
    (a.out/"manifest.json").write_text(json.dumps(
        {"version":VERSION,"as_of":str(END),"source_manifest_sha256":result["source_manifest_sha256"],
         "files_sha256":hash1},ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({"status":"RESEARCH_DESCRIPTIVE_NO_TRADES",
        "counts":{p:{h:result["pooled"][str(h)][p]["labels"]
                    for h in HORIZONS} for p in PERIODS},
        "rows":len(result["rows"])},ensure_ascii=False))


if __name__=="__main__":
    main()
