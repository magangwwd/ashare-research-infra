"""Expanded-stock, frozen-volatility market-state and genuinely lagged pre20 study.

Market proxy: equal-weight daily returns of 240 deterministic SHA256-selected
2023-10-16 eligible main-board tickers PLUS the five preselected tickers.
The proxy is an exploratory STOCK PANEL, not CSI300 nor a tradable benchmark.
Signal uses the exact unchanged kline_three_year.indicators() rule.
All predictive attributes are known by signal-day close; market prior20 and
stock prior20 both EXCLUDE the signal session. No lookahead in conditioning.
All future prices are evaluation labels only; no fill or profit assertions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from ai_research.kline_three_year import START, END, indicators
from ai_research.five_stock_pre_move_kline import STOCKS, features_from_gold

VERSION="VOL_MARKET_DAY_PRE20_PANEL_V1"
EXTRA_SAMPLE_SIZE=240
MARKET_BAND=0.03
DAY_BAND=0.02
TARGET_LOWER=0.05
TARGET_UPPER=0.10
HORIZON=5
YEAR_WINDOWS=("2023_2024","2025","2026")
FIELDS=("open","high","low","close","volume_shares")
FIXED_PANEL_SHA="hashlib.sha256(symbol).hexdigest() ascending on 2023-10-16 eligible tickers"


def when(t):
    d=str(pd.Timestamp(t).date())
    return ("2023_2024" if d<="2024-12-31" else
            "2025" if d<="2025-12-31" else "2026")


def bin_prior20(r):
    return ("<0%" if r<0 else
            "0-5%" if r<TARGET_LOWER else
            "5-10%" if r<TARGET_UPPER else
            "10-20%" if r<.20 else ">=20%")


def market_state(r):
    return ("DOWN" if r<=-MARKET_BAND else
            "UP" if r>=MARKET_BAND else "SIDEWAYS")


def day_state(r):
    return ("DOWN_2PLUS" if r<=-DAY_BAND else
            "UP_2PLUS" if r>=DAY_BAND else "FLAT_2")


def read_calendar(root):
    c=pd.read_parquet(root/"trading_calendar.parquet")
    c["calendar_date"]=pd.to_datetime(c.calendar_date)
    if "is_open" in c.columns:
        c=c.loc[c.is_open.eq(True)]
    dates=pd.DatetimeIndex(c.loc[
        (c.calendar_date>=pd.Timestamp("2023-05-01"))&
        (c.calendar_date<=pd.Timestamp(END)),"calendar_date"].unique()).sort_values()
    if len(dates)<800 or dates[-1].date()!=END:
        raise ValueError("Exchange trading calendar does not include 2026-10-16")
    return dates


def choose_universe(root):
    oct_price=root/"daily_prices/year=2023/month=10/data.parquet"
    oct_factor=root/"adjustment_factors/year=2023/month=10/data.parquet"
    if not oct_price.exists() or not oct_factor.exists():
        raise ValueError("October 2023 anchor partitions missing")
    px=pd.read_parquet(oct_price,columns=[
        "vendor_symbol","trade_date","close","is_quality_valid"])
    fac=pd.read_parquet(oct_factor,columns=[
        "vendor_symbol","trade_date","adjustment_factor"])
    anchor=pd.Timestamp("2023-10-16")
    px=px.loc[(pd.to_datetime(px.trade_date)==anchor)&
              (px.is_quality_valid==True)&(px.close>0)]
    fac=fac.loc[(pd.to_datetime(fac.trade_date)==anchor)&
                (fac.adjustment_factor>0)]
    eligible=set(px.vendor_symbol.astype(str))&set(fac.vendor_symbol.astype(str))
    if not set(STOCKS).issubset(eligible):
        raise ValueError("Fixed five tickers not fully tradeable at anchor")
    extra=sorted(eligible.difference(STOCKS),
                 key=lambda s:(hashlib.sha256(s.encode()).hexdigest(),s))
    if len(extra)<EXTRA_SAMPLE_SIZE:
        raise ValueError("Cannot form precommitted extra 240-stock panel")
    selected=list(STOCKS)+extra[:EXTRA_SAMPLE_SIZE]
    return selected,{"universe_anchor":"2023-10-16",
        "eligible_symbols_on_anchor":len(eligible),
        "selected_symbols":len(selected),
        "additional_symbols":EXTRA_SAMPLE_SIZE,
        "selection_rule":FIXED_PANEL_SHA,
        "selected_symbols_sha256":hashlib.sha256(
            "\n".join(selected).encode()).hexdigest(),
        "selected_symbol_list":selected}


def load_gold_panel(root,selected):
    pfiles=sorted((root/"daily_prices").glob("year=*/month=*/data.parquet"))
    ffiles=sorted((root/"adjustment_factors").glob("year=*/month=*/data.parquet"))
    if len(pfiles)<45 or len(ffiles)<45:
        raise ValueError("Not all Gold price/factor partitions present")
    pxcols=["vendor_symbol","trade_date",*FIELDS,"is_quality_valid"]
    faccols=["vendor_symbol","trade_date","adjustment_factor"]
    opts=[("vendor_symbol","in",selected)]
    prices=pd.concat((pd.read_parquet(p,columns=pxcols,filters=opts)
        for p in pfiles),ignore_index=True)
    factors=pd.concat((pd.read_parquet(p,columns=faccols,filters=opts)
        for p in ffiles),ignore_index=True)
    for frame in (prices,factors):
        frame["trade_date"]=pd.to_datetime(frame.trade_date)
    prices=prices.loc[prices.trade_date.le(pd.Timestamp(END))].copy()
    factors=factors.loc[factors.trade_date.le(pd.Timestamp(END))].copy()
    keys=["vendor_symbol","trade_date"]
    if (prices.duplicated(keys).any() or factors.duplicated(keys).any()):
        raise ValueError("Duplicate Gold price/factor stock-date keys")
    joined=prices.merge(factors,on=keys,how="left",validate="one_to_one")
    bad=(~joined.is_quality_valid.fillna(False)) | (
        ~np.isfinite(joined[[*FIELDS,"adjustment_factor"]]).all(axis=1)) | (
        joined.adjustment_factor.le(0)) | (
        joined.low.gt(joined.high)) | (joined.open.le(0)) | (joined.close.le(0))
    joined.loc[bad,[*FIELDS,"adjustment_factor"]]=np.nan
    return {symbol:frame.set_index("trade_date").sort_index().drop(
        columns=["vendor_symbol","is_quality_valid"]) for symbol,frame in
        joined.groupby("vendor_symbol",sort=False)},{
        "bad_gold_rows":int(bad.sum()),"price_partitions":len(pfiles),
        "factor_partitions":len(ffiles)}


def build_market_proxy(stock_frames,chosen,calendar):
    missing=[symbol for symbol in chosen if symbol not in stock_frames]
    if missing:
        raise ValueError(f"Missing precommitted panel symbol {missing[:3]}")
    close=pd.concat({
        symbol:(stock_frames[symbol]["close"]*
                 stock_frames[symbol]["adjustment_factor"]).reindex(calendar)
        for symbol in chosen
    },axis=1)
    daily=close.pct_change(fill_method=None)
    active=daily.notna().sum(axis=1)
    min_active=int(np.ceil(len(chosen)*0.65))
    equal_weighted=daily.mean(axis=1).where(active>=min_active)
    # T-20 .. T-1, excludes T; every daily constituent must have reported data.
    prior20=((1+equal_weighted).rolling(20,min_periods=20)
        .apply(np.prod,raw=True).shift(1)-1)
    return pd.DataFrame({
        "market_eqw_day_ret":equal_weighted,
        "market_eqw_prior20_excl_day":prior20,
        "market_sample_active":active,
    }),{"proxy":"Fixed 2023-10-16 SHA256 market sampling; daily-rebalanced uninvestable equal-weight basket",
        "min_active_to_accept":min_active,
        "non_null_prior20_days":int(prior20.notna().sum()),
        "first_prior20_date":str(prior20.dropna().index[0].date())
              if prior20.notna().any() else None}


def events_by_symbol(frame,calendar,symbol,market):
    prices=frame.reindex(calendar)
    p,labels=indicators(prices)
    original_features,_,_=features_from_gold(prices)
    c=p.adj_close
    prev20=c.shift(1)/c.shift(21)-1
    incl20=c/c.shift(20)-1
    dayret=c/c.shift(1)-1
    future=c.shift(-5)/c-1
    records=[]
    for idx in np.flatnonzero(labels["volatility_expansion"].to_numpy()):
        if idx+5>=len(calendar) or original_features.iloc[idx].isna().any():
            continue
        d=calendar[idx]
        if d.date()<START or d.date()>END or when(d)!=when(calendar[idx+5]):
            continue
        five_low=p.adj_low.iloc[idx+1:idx+6]
        future_c=c.iloc[idx:idx+6]
        next_open=p.adj_open.iloc[idx+1]
        markt=market.iloc[idx]
        vals=[prev20.iloc[idx],incl20.iloc[idx],dayret.iloc[idx],
              future.iloc[idx],next_open,markt.market_eqw_prior20_excl_day]
        if (not np.isfinite(vals).all() or next_open<=0 or
            len(five_low)!=5 or five_low.isna().any() or future_c.isna().any()):
            continue
        day_return=float(dayret.iloc[idx])
        pre20=float(prev20.iloc[idx])
        incl=float(incl20.iloc[idx])
        mret=float(markt.market_eqw_prior20_excl_day)
        min_vs_next=float(five_low.min()/next_open-1)
        records.append({
            "symbol":symbol,"day":str(d.date()),"period":when(d),
            "session_idx":int(idx),
            "pre20_excl_signal":round(pre20,7),
            "prior20_including_signal":round(incl,7),
            "signal_day_return":round(day_return,7),
            "market_prior20_return":round(mret,7),
            "pre20_bin":bin_prior20(pre20),
            "legacy20_bin":bin_prior20(incl),
            "same_legacy_target":bool(TARGET_LOWER<=incl<TARGET_UPPER),
            "prior20_target":bool(TARGET_LOWER<=pre20<TARGET_UPPER),
            "market_phase":market_state(mret),
            "signal_direction":day_state(day_return),
            "signal_up_at_least_2":day_return>=DAY_BAND,
            "future5_close":round(float(future.iloc[idx]),7),
            "next_open_to_t5":round(float(c.iloc[idx+5]/next_open-1),7),
            "five_day_low_from_next_open":round(min_vs_next,7),
            "intrawindow_down5":min_vs_next<=-.05,
            "day5_down5":float(future.iloc[idx])<=-.05,
        })
    return records


def select_nonoverlap(rows):
    """Greedy 5-session spacing per stock, before applying post-hoc group filters."""
    out=[]
    last={}
    for row in sorted(rows,key=lambda r:(r["symbol"],r["session_idx"])):
        prev=last.get(row["symbol"],-100000)
        if row["session_idx"]-prev>5:
            out.append(row)
            last[row["symbol"]]=row["session_idx"]
    return out


def summary(rows):
    n=len(rows)
    if not n:
        return {"n":0,"mean5":None,"mean_next_open":None,
                "up_rate":None,"intrawindow_down5_rate":None}
    return {
        "n":n,"mean5":round(float(np.mean([r["future5_close"] for r in rows])),6),
        "median5":round(float(np.median([r["future5_close"] for r in rows])),6),
        "mean_next_open":round(float(np.mean([r["next_open_to_t5"] for r in rows])),6),
        "up_rate":round(float(np.mean([r["future5_close"]>0 for r in rows])),6),
        "intrawindow_down5_rate":round(float(np.mean([r["intrawindow_down5"] for r in rows])),6),
        "intrawindow_down5_n":sum(r["intrawindow_down5"] for r in rows),
        "day5_down5_n":sum(r["day5_down5"] for r in rows),
        "distinct_symbols":len(set(r["symbol"] for r in rows)),
        "distinct_signal_dates":len(set(r["day"] for r in rows)),
    }


def summarize_groups(rows):
    groups={}
    settings={
        "legacy_5to10":[lambda r:r["same_legacy_target"]],
        "correct_pre20_5to10":[lambda r:r["prior20_target"]],
        "pre20_regime":{
            "PRE_DOWN":lambda r:r["pre20_excl_signal"]<0,
            "PRE_0_5":lambda r:0<=r["pre20_excl_signal"]<.05,
            "PRE_5_10":lambda r:r["prior20_target"],
            "PRE_10_20":lambda r:.10<=r["pre20_excl_signal"]<.20,
            "PRE_20PLUS":lambda r:r["pre20_excl_signal"]>=.20,
        },
        "market_phase":{x:(lambda r,x=x:r["market_phase"]==x)
                        for x in ("UP","SIDEWAYS","DOWN")},
        "signal_day":{x:(lambda r,x=x:r["signal_direction"]==x)
                       for x in ("DOWN_2PLUS","FLAT_2","UP_2PLUS")},
    }
    for period in ("all",)+YEAR_WINDOWS:
        data=[r for r in rows if period=="all" or r["period"]==period]
        pdata=[r for r in data if r["prior20_target"]]
        ldata=[r for r in data if r["same_legacy_target"]]
        groups[period]={
            "all_vol_expansion":summary(data),
            "legacy_5to10":summary(ldata),
            "correct_pre20_5to10":summary(pdata),
            "legacy_vs_correct_overlap":{
                "both":sum(r["prior20_target"] and r["same_legacy_target"] for r in data),
                "legacy_only":sum(r["same_legacy_target"] and not r["prior20_target"] for r in data),
                "correct_only":sum(r["prior20_target"] and not r["same_legacy_target"] for r in data),
            },
            "all_by_pre20":{k:summary([r for r in data if fn(r)])
                      for k,fn in settings["pre20_regime"].items()},
            "pre20_5to10_by_market":{
                phase:summary([r for r in pdata if r["market_phase"]==phase])
                for phase in ("UP","SIDEWAYS","DOWN")},
            "pre20_5to10_by_signal_day":{
                phase:summary([r for r in pdata if r["signal_direction"]==phase])
                for phase in ("DOWN_2PLUS","FLAT_2","UP_2PLUS")},
            "pre20_5to10_market_and_day":{
                f"{m}|{d}":summary([r for r in pdata
                    if r["market_phase"]==m and r["signal_direction"]==d])
                for m in ("UP","SIDEWAYS","DOWN")
                for d in ("DOWN_2PLUS","FLAT_2","UP_2PLUS")},
        }
    return groups


def validate_original_five(panel_events,original):
    if original.get("version")!="FIVE_STOCK_VOL_PRE20_BIN_DEEP_V1":
        raise ValueError("Original 133-signal archive not expected")
    original_events=original.get("focus_individual_events",[])
    if len(original_events)!=17:
        raise ValueError("Original 17 target samples missing")
    five=[r for r in panel_events if r["symbol"] in STOCKS]
    if len(five)!=133:
        raise ValueError(f"Gold/selection no longer reproduces 133 fixed signals: {len(five)}")
    original_set={(r["stock"],r["day"]) for r in original_events}
    focus={(r["symbol"],r["day"]) for r in five if r["same_legacy_target"]}
    if original_set!=focus:
        raise ValueError(f"Unexpected change in original 5–10% events ({len(focus)})")
    return {"fixed_five_signal_count":len(five),
            "original_focus_5to10_reproduced":len(focus),
            "original_frozen_events_consistent":True}


def generate(root,original):
    calendar=read_calendar(root)
    chosen,selection=choose_universe(root)
    raw,qa=load_gold_panel(root,chosen)
    market,market_meta=build_market_proxy(raw,chosen,calendar)
    if market_meta["non_null_prior20_days"]<700:
        raise ValueError("Historical market proxy coverage insufficient")
    combined=[]
    for i,symbol in enumerate(chosen):
        combined.extend(events_by_symbol(raw[symbol],calendar,symbol,market))
        if (i+1)%50==0:print(f"Processed {i+1}/{len(chosen)} symbols")
    fivecheck=validate_original_five(combined,original)
    solo=[r for r in combined if r["symbol"] in STOCKS]
    isolated=select_nonoverlap(combined)
    return {
        "version":VERSION,"start":str(START),"as_of":str(END),
        "universe":selection,
        "market_proxy":market_meta,
        "gold":qa,
        "reference_five_verification":fivecheck,
        "definitions":{
            "freeze_rule":"unchanged kline_three_year.indicators.volatility_expansion",
            "market":"daily equal-weight proxy of frozen sampled 245 symbols; prior20 ending T-1; UP >=+3%, DOWN <=-3%",
            "pre20_excl_signal":"adjusted close(T-1)/adjusted close(T-21)-1; target [5%,10%)",
            "legacy20_incl_signal":"adjusted close(T)/adjusted close(T-20)-1; target [5%,10%)",
            "signal_day_return":"adjusted close(T)/adjusted close(T-1)-1; UP>=+2%, DOWN<=-2%, else FLAT",
            "outcome":"T+5 adj close/T adj close-1, next-open-to-T+5, min next-five adjusted lows / next-open-1",
        },
        "reference_five":summarize_groups(solo),
        "expanded_panel":summarize_groups(combined),
        "expanded_panel_nonoverlap":summarize_groups(isolated),
        "events":combined,
        "data_leakage_warnings":[
            "Market proxy constituent selection fixed at 2023-10-16: no screening on later stock returns",
            "Proxy is NOT the CSI300, SSE, or actual investable benchmark; not sector neutral",
            "2025 and 2026 historical data has been inspected previously; NOT untouched OOS",
            "2023-24, 2025, 2026 result bins and year checks are retrospective",
            "Co-occurring stock events on one date are highly dependent; per-stock nonoverlap does NOT remove this dependence",
            "Broad panel is one deterministic sample rather than a randomized multi-seed inference",
            "Signal known after T close, next open is an optimistic proxy; fees/slippage/limits/T+1 absent",
            "Daily intrawindow low is an observed worst excursion, not a guaranteed executable stop price",
            "Not a trade recommendation and R1 existing production selection unchanged",
        ],
        "authorization":"RESEARCH_ONLY_NO_TRADING",
    }


def pct(x):
    return "—" if x is None else f"{x:+.2%}"


def fmt(s):
    return f"{s.get('n',0)}次，5日 {pct(s.get('mean5'))}，次日开盘后 {pct(s.get('mean_next_open'))}，期间下探≥5% {pct(s.get('intrawindow_down5_rate'))}"


def report(out):
    lines=[
        "# 波动扩大：市场阶段 × 当天涨幅 × 独立前20日涨幅",
        "",
        f"**研究范围**：五股基准 + {out['universe']['additional_symbols']}只确定性扩样股；"
        "截至2026-10-16；仅日线、无交易。",
        "**市场阶段**为固定股票篮子日均收益构建的代理指标，不等同沪深300或全A指数。",
        "",
        "## 一、原前20日口径和修正后的口径会改变哪些事件？",
        "| 期间/样本 | 原含T条件 | 真正T-1独立20日条件 | 两条件同时满足 |",
        "|---|---|---|---:|"
    ]
    for cohort in ("reference_five","expanded_panel"):
        for period in ("all","2025","2026"):
            d=out[cohort][period]
            lines.append(f"| {cohort}/{period} | {fmt(d['legacy_5to10'])} | "
                f"{fmt(d['correct_pre20_5to10'])} | "
                f"{d['legacy_vs_correct_overlap']['both']} |")
    lines+=["","## 二、真正前20日上涨5%～10%的事件：市场阶段",
        "| 范围 | 年度 | 上涨市场 | 横盘市场 | 下跌市场 |",
        "|---|---|---|---|---|"]
    for cohort in ("reference_five","expanded_panel"):
        for period in ("2023_2024","2025","2026"):
            g=out[cohort][period]["pre20_5to10_by_market"]
            lines.append(f"| {cohort} | {period} | "
                +" | ".join(fmt(g[phase]) for phase in ("UP","SIDEWAYS","DOWN"))+" |")
    lines+=["","## 三、真正前20日上涨5%～10%的事件：当天方向",
        "| 范围 | 年度 | 当天跌≥2% | 当天涨跌<2% | 当天涨≥2% |",
        "|---|---|---|---|---|"]
    for cohort in ("reference_five","expanded_panel"):
        for period in ("2023_2024","2025","2026"):
            g=out[cohort][period]["pre20_5to10_by_signal_day"]
            lines.append(f"| {cohort} | {period} | "
                +" | ".join(fmt(g[phase]) for phase in ("DOWN_2PLUS","FLAT_2","UP_2PLUS"))+" |")
    lines+=["","## 四、扩样后：市场阶段和当天方向交叉",
        "| 年度 | 市场 | 当天方向 | 样本 | 五日均值 | 次日开盘口径 | 期间跌≥5% |",
        "|---|---|---|---:|---:|---:|---:|"]
    for period in ("2023_2024","2025","2026"):
        groups=out["expanded_panel"][period]["pre20_5to10_market_and_day"]
        for m in ("UP","SIDEWAYS","DOWN"):
            for d in ("DOWN_2PLUS","FLAT_2","UP_2PLUS"):
                a=groups[f"{m}|{d}"]
                lines.append(f"| {period} | {m} | {d} | {a['n']} | "
                    f"{pct(a['mean5'])} | {pct(a['mean_next_open'])} | "
                    f"{pct(a['intrawindow_down5_rate'])} |")
    lines+=["","## 五、审计与限制",
        f"- 原五股事件校验：{out['reference_five_verification']}",
        f"- 扩样股票总数：{out['universe']['selected_symbols']}；来源固定在"
        f"{out['universe']['universe_anchor']}，哈希选择，不根据回测结果挑选股票。",
        "- 严格前20日涨幅不含信号当天；市场涨跌阶段也不含信号当天。",
        "- 单组少于20次只做示例，不作方向预测推断；最多只是后续新数据的检验假设。",
        "- 重叠与同日共振、复权、停牌、涨跌停、交易费用均需后续正式回测处理。",
        "",
        *[f"- {x}" for x in out["data_leakage_warnings"]]
    ]
    return "\n".join(lines)+"\n"


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--gold-root",type=Path,required=True)
    p.add_argument("--gold-manifest",type=Path,required=True)
    p.add_argument("--prior-five",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True)
    a=p.parse_args()
    m=json.loads(a.gold_manifest.read_text())
    if (m.get("as_of_date","")<str(END) or
        m.get("validation",{}).get("primary_keys_unique") is not True or
        m.get("validation",{}).get("ohlc_valid") is not True):
        raise ValueError("Quality-verified Gold required")
    orig=json.loads(a.prior_five.read_text())
    doc=generate(a.gold_root,orig)
    doc["provenance_sha256"]={
        "gold_manifest":hashlib.sha256(a.gold_manifest.read_bytes()).hexdigest(),
        "original_five":hashlib.sha256(a.prior_five.read_bytes()).hexdigest(),
    }
    a.out.mkdir(parents=True,exist_ok=True)
    (a.out/"statistics.json").write_text(json.dumps(
        doc,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    pd.DataFrame(doc["events"]).to_csv(a.out/"event_details.csv",index=False)
    (a.out/"report.md").write_text(report(doc),encoding="utf-8")
    sha={f.name:hashlib.sha256(f.read_bytes()).hexdigest()
        for f in a.out.iterdir() if f.is_file()}
    (a.out/"manifest.json").write_text(json.dumps({
        "version":VERSION,"source_sha256":doc["provenance_sha256"],
        "files_sha256":sha},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"status":"DESCRIPTIVE_NO_ORDER",
        "universe":doc["universe"]["selected_symbols"],
        "expanded_events":len(doc["events"]),
        "expanded_focus":doc["expanded_panel"]["all"]["correct_pre20_5to10"]["n"],
        "five_reference":doc["reference_five_verification"]},
        ensure_ascii=False))


if __name__=="__main__":
    main()
