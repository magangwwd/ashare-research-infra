"""Link fixed 600351.SH daily signals to validated 15-minute history (research only).

No data download or trading. A missing minute corpus returns NOT_READY, never
a fabricated zero-return experiment. Daily pattern labels remain frozen in
ai_research.kline_three_year; intraday features are computed at the *close* of
the signal session, and later-session observations are outcomes only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from ai_research.kline_three_year import (
    END, START, TARGET, indicators, read_inputs,
)

VERSION = "KLINE_600351_15M_JOIN_V1"
WANTED = ("volume_spike_up", "first_close_above_20d_high")
SLOTS = [
    *(f"{h:02d}:{m:02d}" for h in (9, 10, 11)
      for m in (0, 15, 30, 45)
      if "09:45" <= f"{h:02d}:{m:02d}" <= "11:30"),
    *(f"{h:02d}:{m:02d}" for h in (13, 14, 15)
      for m in (0, 15, 30, 45)
      if "13:15" <= f"{h:02d}:{m:02d}" <= "15:00"),
]
# Explicit China market BAR END times, without vendor's optional 09:30 auction.
SLOTS = ["09:45", "10:00", "10:15", "10:30", "10:45", "11:00", "11:15", "11:30",
         "13:15", "13:30", "13:45", "14:00", "14:15", "14:30", "14:45", "15:00"]


def analyze_intraday_minute_rows(raw):
    """Normalize two documented vendor schemas and admit only 16-bar sessions."""
    if raw.empty:
        return pd.DataFrame(), {"input_rows": 0, "accepted_days": 0,
                                "duplicate_bars": 0, "rejected_days": 0,
                                "auction_rows_excluded": 0}
    missing = set(("trade_time", "open", "high", "low", "close")) - set(raw)
    if missing:
        raise ValueError("Missing 15m OHLC fields: " + ",".join(sorted(missing)))
    volume_col = "volume_shares" if "volume_shares" in raw else (
        "vol" if "vol" in raw else "volume" if "volume" in raw else None)
    if not volume_col:
        raise ValueError("Missing 15m volume field")
    code_col = "vendor_symbol" if "vendor_symbol" in raw else (
        "ts_code" if "ts_code" in raw else None)
    if not code_col:
        raise ValueError("Missing 15m stock code field")
    stock = raw.loc[raw[code_col].eq(TARGET)].copy()
    if stock.empty:
        raise ValueError("Minutes exist but 600351.SH is absent; not a valid 0 sample")
    ts = pd.to_datetime(stock["trade_time"], errors="coerce")
    if ts.isna().any():
        raise ValueError("Invalid 15m trade_time")
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_convert("Asia/Shanghai").dt.tz_localize(None)
    stock["date"] = ts.dt.normalize()
    stock["hhmm"] = ts.dt.strftime("%H:%M")
    stock["volume"] = pd.to_numeric(stock[volume_col], errors="coerce")
    for c in ("open", "high", "low", "close"):
        stock[c] = pd.to_numeric(stock[c], errors="coerce")
    auction = int((stock["hhmm"] == "09:30").sum())
    stock = stock.loc[stock["hhmm"].isin(SLOTS)]
    duplicates = int(stock.duplicated(["date", "hhmm"]).sum())
    if duplicates:
        raise ValueError("Duplicate 15m bar key: cannot resolve source revision safely")
    results = []
    days_seen = int(stock["date"].nunique())
    for day, frame in stock.groupby("date", sort=True):
        frame = frame.set_index("hhmm").reindex(SLOTS)
        valid = (frame["close"].notna().all()
                 and frame["volume"].notna().all()
                 and (frame["volume"] >= 0).all()
                 and frame["volume"].sum() > 0
                 and (frame[["open", "high", "low", "close"]] > 0).all().all()
                 and (frame["high"] >= frame[["open", "close", "low"]].max(axis=1)).all()
                 and (frame["low"] <= frame[["open", "close", "high"]].min(axis=1)).all())
        if not valid:
            continue
        vols = frame["volume"]
        last30 = frame.loc["14:45", "open"]
        results.append({
            "date": day,
            "raw_last_close": float(frame.loc["15:00", "close"]),
            "first_open": float(frame.loc["09:45", "open"]),
            "first_30m_return": float(frame.loc["10:00", "close"] /
                                       frame.loc["09:45", "open"] - 1),
            "last_30m_return": float(frame.loc["15:00", "close"] / last30 - 1),
            "afternoon_vol_share": float(vols.loc["13:15":].sum() / vols.sum()),
            "last_hour_vol_share": float(vols.loc["14:15":].sum() / vols.sum()),
            "minutes_volume": float(vols.sum()),
        })
    return pd.DataFrame(results).set_index("date") if results else pd.DataFrame(), {
        "input_rows": int(len(raw)), "target_rows": int(len(stock)),
        "accepted_days": len(results), "rejected_days": days_seen - len(results),
        "duplicate_bars": duplicates, "auction_rows_excluded": auction,
        "expected_bars_per_accepted_day": 16,
    }


def read_minute_partitions(root: Path):
    paths = sorted(root.glob("year=*/month=*/data.parquet"))
    if not paths:
        return pd.DataFrame(), 0
    out = []
    for path in paths:
        df = pd.read_parquet(path)
        col = "vendor_symbol" if "vendor_symbol" in df else (
            "ts_code" if "ts_code" in df else None)
        if not col:
            raise ValueError("Minute dataset missing symbol; refuse source silently")
        chunk = df.loc[df[col].eq(TARGET)]
        if not chunk.empty:
            out.append(chunk.copy())
    if not out:
        raise ValueError("Minute partitions exist but no target symbol")
    return pd.concat(out, ignore_index=True), len(paths)


def compute(daily, minute_raw):
    """Returns coverage first, observations only when enough verified minute days."""
    minute, quality = analyze_intraday_minute_rows(minute_raw)
    p, patterns = indicators(daily)
    start, end = pd.Timestamp(START), pd.Timestamp(END)
    valid_close = p["adj_close"].loc[start:end].notna()
    valid_daily_dates = p.loc[start:end].index[valid_close]
    daily_count = int(len(valid_daily_dates))
    if daily_count < 500:
        raise ValueError("Daily Gold too incomplete for three-year baseline")
    if minute.empty:
        return {
            "status": "NO_15M_DATA",
            "source_quality": quality, "daily_valid_days": daily_count,
            "minute_daily_coverage": 0.0, "signal_details": [],
            "patterns": {}, "risk": ["Minute source absent; no conclusions possible"]
        }
    joined = minute.reindex(valid_daily_dates)
    rawclose = p.loc[valid_daily_dates, "close"]
    close_match = (joined["raw_last_close"].notna() &
                   (joined["raw_last_close"] - rawclose).abs() <=
                   np.maximum(.015, rawclose.abs()*.001))
    mismatched = joined["raw_last_close"].notna() & ~close_match
    dropped_mismatch = int(mismatched.sum())
    quality["daily_close_mismatch_days"] = dropped_mismatch
    bad_dates = valid_daily_dates[mismatched]
    quality["daily_close_mismatch_by_year"] = {
        str(year):int(sum(bad_dates.year == year)) for year in sorted(set(bad_dates.year))
    }
    quality["daily_close_mismatch_examples"] = [
        {"date":str(day.date()),"daily_raw":round(float(rawclose.loc[day]),4),
         "minute_last_raw":round(float(joined.loc[day,"raw_last_close"]),4),
         "difference_pct":round(float(joined.loc[day,"raw_last_close"] /
                                      rawclose.loc[day]-1),5)}
        for day in bad_dates[:20]]
    joined.loc[~close_match, :] = np.nan
    valid_minute = joined["raw_last_close"].notna()
    minute_dates = valid_daily_dates[valid_minute]
    cover = round(float(valid_minute.mean()), 5)
    per_period = {}
    windows = {
        "discovery": (date(2023, 10, 16), date(2024, 12, 31)),
        "validation": (date(2025, 1, 1), date(2025, 12, 31)),
        "test": (date(2026, 1, 1), date(2026, 10, 16)),
    }
    for key, (first, last) in windows.items():
        dates = valid_daily_dates[(valid_daily_dates.date >= first) &
                                  (valid_daily_dates.date <= last)]
        n = int(joined.loc[dates, "raw_last_close"].notna().sum())
        per_period[key] = {"minute_days": n, "daily_days": len(dates),
                           "coverage": round(n/len(dates), 5) if len(dates) else None}
    # If no year is near-complete, selection bias dominates all timing comparisons.
    coverage_pass = (cover >= .90 and all(
        v["coverage"] is not None and v["coverage"] >= .85
        for v in per_period.values()))
    details, comparisons = [], {}
    future5 = (p["adj_close"].shift(-5) / p["adj_close"] - 1)
    future5_end = pd.Series(p.index,index=p.index).shift(-5)
    # Next scheduled SSE session, never jump over a suspended/missing daily bar.
    all_market_days = p.index
    next_day = {all_market_days[i]:all_market_days[i+1]
                for i in range(len(all_market_days)-1)}
    cutoff_by_period = {
        "discovery":pd.Timestamp("2024-12-31"),
        "validation":pd.Timestamp("2025-12-31"),
        "test":pd.Timestamp("2026-10-16"),
    }
    for pattern in WANTED:
        rows = []
        for day in minute_dates:
            if not bool(patterns[pattern].get(day, False)):
                continue
            # Future five-trading-day outcome remains a separate label, not a feature.
            observed = future5.get(day, np.nan)
            period = ("discovery" if day.date() <= date(2024,12,31) else
                      "validation" if day.date() <= date(2025,12,31) else "test")
            if (not np.isfinite(observed) or pd.isna(future5_end.get(day))
                    or future5_end[day] > cutoff_by_period[period]):
                continue
            nxday = next_day.get(day)
            x = joined.loc[day]
            nx = joined.loc[nxday] if nxday in joined.index else None
            record = {
                "signal_date": str(day.date()), "pattern": pattern,
                "afternoon_vol_share": round(float(x.afternoon_vol_share), 5),
                "last_hour_vol_share": round(float(x.last_hour_vol_share), 5),
                "first_30m_return": round(float(x.first_30m_return), 5),
                "last_30m_return": round(float(x.last_30m_return), 5),
                "future_5d_close_to_close_return": round(float(observed), 5),
                "next_session_first_30m_return": (
                    round(float(nx.first_30m_return), 5)
                    if nx is not None and pd.notna(nx.first_30m_return) else None),
                "period": period,
                "entry_executability": "NOT_TESTED",
            }
            rows.append(record)
        details.extend(rows)
        groups = {}
        for slice_name, cond in (
            ("afternoon_dominant_ge55pct", lambda v: v["afternoon_vol_share"] >= .55),
            ("last_hour_dominant_ge30pct", lambda v: v["last_hour_vol_share"] >= .30),
        ):
            groups[slice_name] = {}
            for period in windows:
                relevant = [r for r in rows if r["period"] == period]
                selected = [r for r in relevant if cond(r)]
                def summary(items):
                    returns = [r["future_5d_close_to_close_return"] for r in items]
                    morning = [r["next_session_first_30m_return"] for r in items
                               if r["next_session_first_30m_return"] is not None]
                    return {"count":len(items),
                            "mean_future5d":round(float(np.mean(returns)),5) if returns else None,
                            "future5d_up_rate":round(float(np.mean(np.array(returns)>0)),5)
                                if returns else None,
                            "mean_next_session_first30":round(float(np.mean(morning)),5)
                                if morning else None,
                            "next_morning_observed_count":len(morning)}
                groups[slice_name][period] = {
                    "all_events":summary(relevant),
                    "selected":summary(selected),
                    "not_selected":summary([r for r in relevant if not cond(r)]),
                    "sample_sufficient_for_hypothesis":(
                        len(selected)>=10 and len(relevant)-len(selected)>=10),
                }
        comparisons[pattern] = {"events_with_verified_minutes":len(rows),
                                "split":groups}
    return {
        "status": "EXPLORATORY_COVERAGE_PASSED_NOT_PROVEN" if coverage_pass
                  else "INCOMPLETE_MINUTE_COVERAGE_NO_CONCLUSIONS",
        "source_quality": quality, "daily_valid_days": daily_count,
        "minute_valid_days_aligned":len(minute_dates),
        "minute_daily_coverage":cover, "coverage_by_period":per_period,
        "signal_details": details, "patterns": comparisons,
        "risk": ["Not a fill simulation; minute signal features only known after day close",
                 "No backtest inference on days not covered by verified 16-bar source",
                 "Some events share future five days: statistical dependence",
                 "Thresholds fixed before minute hypothesis review; future OOS essential"],
    }


def write(report, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    (output/"statistics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    lines = [
        "# 亚宝药业日线×15分钟研究（只读）",
        "", f"状态：**{report['status']}**",
        f"日线有效交易日：{report['daily_valid_days']}",
        f"经过十六根15分钟K线和收盘价核对的交易日：{report.get('minute_valid_days_aligned',0)}",
        f"分钟覆盖率：{report['minute_daily_coverage']:.2%}",
        "", "原有日线结论不变；分钟特征只用于进一步提出假设。",
    ]
    if report.get("coverage_by_period"):
        lines += ["","| 时间段 | 分钟完整日 | 日线有效日 | 覆盖率 |",
                  "|---|---:|---:|---:|"]
        for k,v in report["coverage_by_period"].items():
            lines.append(f"| {k} | {v['minute_days']} | {v['daily_days']} | {v['coverage']:.1%} |")
    for name, val in report.get("patterns",{}).items():
        lines += ["", f"## {name}：匹配到{val['events_with_verified_minutes']}个形态日期"]
        for group, periods in val["split"].items():
            lines.append(f"### {group}")
            for year, data in periods.items():
                x = data["selected"]
                lines.append(f"- {year}：{x['count']}次；随后五日平均{str(x['mean_future5d'])}；"
                             f"下一交易日开盘前30分钟数据{str(x['mean_next_session_first30'])}。"
                             "不是可成交收益。")
    lines += ["", "## 风险与缺口", *["- "+r for r in report["risk"]]]
    (output/"report.md").write_text("\n".join(lines)+"\n",encoding="utf-8")
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in output.iterdir()}
    (output/"manifest.json").write_text(
        json.dumps({"version":VERSION, "symbol":TARGET, "as_of":str(END),
                    "output_sha256":hashes}, indent=2)+"\n",encoding="utf-8")


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--minutes-root",type=Path)
    p.add_argument("--daily-root",type=Path)
    p.add_argument("--quality-only",action="store_true")
    p.add_argument("--output-dir",type=Path,required=True)
    args=p.parse_args()
    rows, partitions=(read_minute_partitions(args.minutes_root)
                      if args.minutes_root else (pd.DataFrame(),0))
    if not partitions:
        result = {
            "version": VERSION, "symbol":TARGET, "as_of":str(END),
            "status":"NO_15M_DATA","daily_valid_days":728,
            "minute_valid_days_aligned":0,"minute_daily_coverage":0.0,
            "source_quality":{"minute_partitions":0},
            "patterns":{},"signal_details":[],
            "risk":["No 15m Parquet in audited Drive source folders",
                    "Daily count 728 is from previously verified daily experiment",
                    "Do not infer or interpolate historical minute bars"],
            "authorization":"RESEARCH_ONLY_NO_TRADING"
        }
    else:
        if args.quality_only or args.daily_root is None:
            raise SystemExit("15m data found; must provide --daily-root to crosscheck")
        days, _=read_inputs(args.daily_root,TARGET,END)
        result=compute(days,rows)
        result.update(version=VERSION, symbol=TARGET, as_of=str(END),
                      authorization="RESEARCH_ONLY_NO_TRADING")
        result["source_quality"]["minute_partitions"]=partitions
    write(result,args.output_dir)
    print(json.dumps({"status":result["status"],
                      "minute_valid_days":result.get("minute_valid_days_aligned",0),
                      "patterns":{k:v["events_with_verified_minutes"]
                                  for k,v in result.get("patterns",{}).items()} }))


if __name__=="__main__":
    main()
