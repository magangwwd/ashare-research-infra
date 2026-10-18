"""Exploratory, purely OHLCV-based 3-year single-stock statistics.

No fundamental/news/AI features, no orders and no claims of predictive power.
Prices are adjusted only by same-date corporate-action factors, not by a
future-base normalized price. Calendar-date future returns require complete
exchange sessions: halted dates are never silently compressed to next trade.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from datetime import date, timedelta
from pathlib import Path
import numpy as np
import pandas as pd

VERSION = "KLINE_3Y_EXPLORATORY_V1"
TARGET = "600351.SH"
HORIZONS = (1, 3, 5, 10, 20)
START = date(2023, 10, 16)
END = date(2026, 10, 16)
MIN_TOTAL = 25
MIN_TEST = 8


def read_inputs(root: Path, symbol: str, as_of: date):
    p_files = sorted((root / "daily_prices").glob("year=*/month=*/data.parquet"))
    f_files = sorted((root / "adjustment_factors").glob("year=*/month=*/data.parquet"))
    if not p_files or not f_files:
        raise ValueError("Monthly source parquet partitions missing")
    cols = ["vendor_symbol", "trade_date", "open", "high", "low", "close",
            "volume_shares", "is_quality_valid"]
    frame = pd.concat(
        (part for p in p_files if not (part := pd.read_parquet(p, columns=cols)
          .query("vendor_symbol == @symbol")).empty), ignore_index=True)
    factors = pd.concat(
        (part for p in f_files if not (part := pd.read_parquet(
          p, columns=["vendor_symbol", "trade_date", "adjustment_factor"])
          .query("vendor_symbol == @symbol")).empty), ignore_index=True)
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    factors["trade_date"] = pd.to_datetime(factors["trade_date"])
    if frame.trade_date.duplicated().any() or factors.trade_date.duplicated().any():
        raise ValueError("Duplicate price/adjustment date")
    if frame.empty or factors.empty:
        raise ValueError("Selected security has no source records")
    frame = frame[frame.trade_date.dt.date <= as_of]
    factors = factors[factors.trade_date.dt.date <= as_of]
    joined = frame.merge(factors[["trade_date", "adjustment_factor"]],
                         on="trade_date", validate="one_to_one", how="left")
    invalid = (~joined.is_quality_valid.fillna(False)) | (
        joined.adjustment_factor.isna()) | (joined.adjustment_factor <= 0)
    invalid |= ~np.isfinite(joined[["open", "high", "low", "close"]]).all(axis=1)
    invalid |= (joined["low"] > joined["high"]) | (joined["open"] <= 0) | (joined["close"] <= 0)
    invalid_count = int(invalid.sum())
    joined.loc[invalid, ["open", "high", "low", "close",
                          "volume_shares", "adjustment_factor"]] = np.nan
    joined = joined.set_index("trade_date").sort_index()
    calendar = pd.read_parquet(root / "trading_calendar.parquet")
    calendar["calendar_date"] = pd.to_datetime(calendar["calendar_date"])
    if "is_open" in calendar.columns:
        calendar = calendar[calendar["is_open"] == True]
    market_days = pd.DatetimeIndex(
        calendar.loc[(calendar.calendar_date >= pd.Timestamp("2023-05-01")) &
                     (calendar.calendar_date <= pd.Timestamp(as_of)), "calendar_date"].unique()
    ).sort_values()
    if market_days.empty or market_days[-1].date() != as_of:
        raise ValueError("Validated Gold as-of is not in source exchange calendar")
    return joined.reindex(market_days), {"invalid_source_rows": invalid_count,
                                        "exchange_sessions": len(market_days),
                                        "source_price_partitions": len(p_files),
                                        "source_factor_partitions": len(f_files)}


def indicators(df):
    p = df.copy()
    for x in ("open", "high", "low", "close"):
        p["adj_" + x] = p[x] * p.adjustment_factor
    close = p.adj_close
    ret = close.pct_change(fill_method=None)
    volume = p.volume_shares
    med20 = volume.rolling(20, min_periods=20).median().shift(1)
    base20 = close.rolling(20, min_periods=20).max().shift(1)
    prior_breakout = close.shift(1) > close.rolling(20, min_periods=20).max().shift(2)
    down3 = (ret.lt(0).rolling(3, min_periods=3).sum().eq(3) &
             ~ret.shift(3).lt(0))
    candle_range = p.adj_high - p.adj_low
    body = (p.adj_close - p.adj_open).abs()
    lower_wick = np.minimum(p.adj_open, p.adj_close) - p.adj_low
    upper_wick = p.adj_high - np.maximum(p.adj_open, p.adj_close)
    hammer = ((candle_range > 0) & (lower_wick >= 2 * body.clip(lower=0.001)) &
              (p.adj_close >= p.adj_low + candle_range * 0.65) &
              (upper_wick <= (body + 0.001)))
    spike_up = (ret >= .01) & (volume >= 1.8 * med20) & (med20 > 0)
    past10 = close.shift(1) / close.shift(11) - 1
    low_vol_pullback = (ret < 0) & (volume < .7 * med20) & (past10 > .05)
    breakout = (close > base20) & ~prior_breakout.fillna(False)
    vol5 = ret.rolling(5, min_periods=5).std()
    vol20 = ret.rolling(20, min_periods=20).std().shift(1)
    expansion = (vol5 > 1.6 * vol20) & (vol5.shift(1) <= 1.6 * vol20.shift(1)) & (vol20 > 0)
    out = {"three_down_days": down3, "long_lower_shadow": hammer,
           "volume_spike_up": spike_up,
           "low_volume_pullback_after_rise": low_vol_pullback,
           "first_close_above_20d_high": breakout,
           "volatility_expansion": expansion}
    for k, v in out.items():
        out[k] = v.fillna(False) & close.notna()
    return p, out


def numbers(df, events, start=START, end=END):
    p, marks = indicators(df)
    close = p.adj_close
    windows = {"discovery": (start, date(2024, 12, 31)),
               "validation": (date(2025, 1, 1), date(2025, 12, 31)),
               "test": (date(2026, 1, 1), end)}
    prices = pd.DatetimeIndex(close.index)
    if end < start or prices[-1].date() < end:
        raise ValueError("Insufficient exchange-calendar history")
    future = {}
    for h in HORIZONS:
        future[h] = close.shift(-h) / close - 1
    groups = {}
    for name, (left, right) in windows.items():
        selection = ((prices.date >= left) & (prices.date <= right))
        groups[name] = {}
        # Event length H must be observed *within its own period* to avoid test leakage.
        for h in HORIZONS:
            last = pd.Series(prices, index=prices).shift(-h)
            groups[name][h] = (selection & (last.dt.date <= right) &
                                (last.dt.date >= left) & future[h].notna().to_numpy() &
                                close.notna().to_numpy())
    results = {}
    for name, mask in marks.items():
        result = {"name": name, "raw_event_count": int(
            (mask & (prices.date >= start) & (prices.date <= end)).sum()),
            "horizons": {}}
        for h in HORIZONS:
            event_data = mask.to_numpy() & groups["discovery"][h]
            # Baseline and event are restricted to same period and same look-ahead availability.
            pieces = {}
            for period in ("discovery", "validation", "test"):
                eligible = groups[period][h]
                tagged = eligible & mask.to_numpy()
                baseline = future[h].to_numpy()[eligible]
                values = future[h].to_numpy()[tagged]
                positions = np.flatnonzero(tagged)
                isolated = sum((j == 0 or positions[j] - positions[j-1] > h)
                               for j in range(len(positions)))
                pieces[period] = {
                    "n": int(len(values)), "isolated_n": int(isolated),
                    "up_probability": round(float((values > 0).mean()), 4) if len(values) else None,
                    "mean_return": round(float(values.mean()), 5) if len(values) else None,
                    "median_return": round(float(np.median(values)), 5) if len(values) else None,
                    "baseline_up_probability": round(float((baseline > 0).mean()), 4)
                        if len(baseline) else None,
                    "baseline_mean_return": round(float(baseline.mean()), 5)
                        if len(baseline) else None,
                    "relative_mean_vs_baseline": round(float(values.mean()-baseline.mean()), 5)
                        if len(values) and len(baseline) else None,
                }
            result["horizons"][str(h)] = pieces
        h5 = result["horizons"]["5"]
        train = h5["discovery"]
        valid = h5["validation"]
        test = h5["test"]
        result["evidence_grade"] = (
            "INSUFFICIENT_EVENTS" if (train["n"] < MIN_TOTAL or
                   valid["n"] < MIN_TEST or test["n"] < MIN_TEST) else
            "UNSTABLE_OUT_OF_SAMPLE" if any(
                x["relative_mean_vs_baseline"] is None or
                x["relative_mean_vs_baseline"] <= 0 for x in (valid, test)) else
            "EXPLORATORY_POSITIVE_NOT_TRADEABLE"
        )
        results[name] = result
    return results, {k: {str(h): int(groups[k][h].sum()) for h in HORIZONS}
                     for k in windows}


def render(report):
    lines = [
        "# 亚宝药业（600351.SH）近三年 K 线事件统计（探索性）",
        "",
        f"**样本时段**：{report['start']} 至 {report['as_of']}，不包含财务、公告与模型推理。",
        f"**数据完整性**：统计交易日 {report['rows_in_study']}；无效源记录 {report['source']['invalid_source_rows']}。",
        "**关键提醒**：先按固定形态定义计算描述性统计，再按时间切分验证；这不是可立即买入的策略。",
        "",
        "## 第一眼看：每种形态出现后五天的表现",
        "",
        "| 形态 | 三年出现次数 | 验证期样本数 | 测试期样本数 | 测试期5日上涨率 | 测试期相对普通日平均收益 | 证据等级 |",
        "|---|---:|---:|---:|---:|---:|---|"
    ]
    for n, d in report["patterns"].items():
        h=d["horizons"]["5"]
        v=h["validation"]
        t=h["test"]
        up=f"{t['up_probability']:.1%}" if t["up_probability"] is not None else "样本不足"
        delta=f"{t['relative_mean_vs_baseline']:+.2%}" if t["relative_mean_vs_baseline"] is not None else "不可判定"
        lines.append(f"| {n} | {d['raw_event_count']} | {v['n']} | {t['n']} | {up} | {delta} | {d['evidence_grade']} |")
    lines.extend([
        "",
        "## 怎样看结果",
        "1. “上涨率”仅代表历史上后五个交易日收盘价高于信号日收盘价的比例。",
        "2. “相对普通日平均收益”是同一测试时间段的条件均值减去所有可评估交易日的均值。",
        "3. 多个信号可能扎堆出现，不是相互独立交易；isolated_n 可用于警惕重复样本。",
        "4. 当日收盘才确认形态，不能假设能在当日收盘买入；没有计入次日开盘滑点、涨跌停或费用。",
        "5. 本实验同时尝试六种形态和五个期限，存在挑结果的偏差；任何积极结果都需冻结规则后用以后真实新数据复验。",
        "6. 复权收盘价用于计算收益，复权因子只取历史逐日值；停牌日不当成跌幅零或提前跳到复牌日。",
        "",
        f"数据源状态：{report['source']['history_manifest_status']}；历史最新数据日 {report['source']['history_max_date']}。",
    ])
    return "\n".join(lines)+"\n"


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--history-status", type=Path, required=True)
    parser.add_argument("--gold-manifest", type=Path, required=True)
    parser.add_argument("--as-of", type=str, default="2026-10-16")
    parser.add_argument("--output-dir", type=Path, required=True)
    args=parser.parse_args()
    as_of=date.fromisoformat(args.as_of)
    if as_of!=END:
        raise SystemExit("V1 research date fixed to 2026-10-16 to prevent moving test window")
    history=json.loads(args.history_status.read_text())
    gold=json.loads(args.gold_manifest.read_text())
    history_date=str(history.get("ended_range", ""))
    if (history.get("status")!="COMPLETE" or history.get("partial_months") or
        history_date < as_of.strftime("%Y%m%d") or
        str(gold.get("as_of_date")) < str(as_of) or
        gold.get("validation",{}).get("primary_keys_unique") is not True or
        gold.get("validation",{}).get("ohlc_valid") is not True):
        raise SystemExit("Reject stale/partial historical Gold input")
    data, meta=read_inputs(args.data_root, TARGET, as_of)
    if (data.loc[(data.index.date>=START)&(data.index.date<=END), "close"]
            .notna().sum() < 500):
        raise SystemExit("Too few actual 3-year closes")
    result, baselines=numbers(data, None)
    out={"version":VERSION, "symbol":TARGET, "start":START.isoformat(),
         "as_of":as_of.isoformat(), "rows_in_study":int(
             ((data.index.date>=START)&(data.index.date<=END)).sum()),
         "valid_trade_sessions":int(data.loc[
             (data.index.date>=START)&(data.index.date<=END),"close"].notna().sum()),
         "source":{**meta, "history_manifest_status":history["status"],
                   "history_max_date":history_date,
                   "gold_sha256":hashlib.sha256(args.gold_manifest.read_bytes()).hexdigest(),
                   "history_status_sha256":hashlib.sha256(args.history_status.read_bytes()).hexdigest()},
         "valid_baseline_days_by_split":baselines, "patterns":result,
         "methodology":"Descriptive close-to-close adjusted returns; no executable PnL, no information after signal date used for features",
         "authorization":"RESEARCH_ONLY_NO_TRADING"}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir/"statistics.json").write_text(
        json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    (args.output_dir/"report.md").write_text(render(out),encoding="utf-8")
    manifests={"symbol":TARGET, "as_of":str(as_of),"source":out["source"],
               "files_sha256":{p.name:hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in args.output_dir.iterdir() if p.is_file()}}
    (args.output_dir/"manifest.json").write_text(
        json.dumps(manifests,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    print(json.dumps({"status":"DESCRIPTIVE_RESEARCH_READY_NOT_TRADE_SIGNAL",
        "symbol":TARGET,"as_of":str(as_of),"sessions":out["valid_trade_sessions"],
        "patterns":{k:v["raw_event_count"] for k,v in result.items()}}))


if __name__ == "__main__":
    main()
