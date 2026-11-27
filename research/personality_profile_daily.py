"""Descriptive PA-v2 daily profile using official pre_close reference prices.

Not fully adjusted total returns, predictive validation, or a fill simulation.
"""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import pandas as pd

TARGET = "600031.SH"
PEERS = ("000157.SZ", "000425.SZ", "600761.SH", "601100.SH")


def prepare(panel):
    required = ["ts_code", "trade_date", "open", "high", "low", "close", "pre_close"]
    missing = set(required) - set(panel)
    if missing:
        raise ValueError("missing columns: " + ",".join(sorted(missing)))
    d = panel.copy()
    d["trade_date"] = pd.to_datetime(d.trade_date, errors="raise")
    if d[["ts_code", "trade_date"]].isna().any().any():
        raise ValueError("missing stock/date")
    if d.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("duplicate stock/date")
    for key in ["open", "high", "low", "close", "pre_close"]:
        d[key] = pd.to_numeric(d[key], errors="raise")
    prices = d[["open", "high", "low", "close"]].to_numpy()
    if not np.isfinite(prices).all() or (prices <= 0).any():
        raise ValueError("invalid OHLC")
    if ((d.low > d[["open", "close"]].min(axis=1))
            | (d.high < d[["open", "close"]].max(axis=1))).any():
        raise ValueError("inconsistent OHLC")
    known = d.pre_close.notna()
    if ((~np.isfinite(d.loc[known, "pre_close"]))
            | (d.loc[known, "pre_close"] <= 0)).any():
        raise ValueError("invalid pre_close")
    d = d.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    # Never fill pre_close from previous raw close: ex-rights reference may differ.
    d["ret"] = d.close / d.pre_close - 1
    d["gap"] = d.open / d.pre_close - 1
    d["intraday"] = d.close / d.open - 1
    d["range"] = (d.high - d.low) / d.pre_close
    return d


def scalar(x):
    return float(x) if pd.notna(x) and np.isfinite(x) else None


def conditional(d, mask, outcome):
    subset = d.loc[mask]
    count = int(outcome.loc[subset.index].sum())
    n = len(subset)
    return {"n": n, "count": count, "rate_pct": 100 * count / n if n else None}


def metrics(d):
    r = d.ret.dropna()
    g = d.gap.dropna()
    return {
        "rows": len(d), "return_n": len(r),
        "missing_pre_close": int(d.pre_close.isna().sum()),
        "daily_std_pct": scalar(r.std(ddof=1)*100),
        "annualized_std_pct": scalar(r.std(ddof=1)*np.sqrt(252)*100),
        "median_abs_daily_return_pct": scalar(r.abs().median()*100),
        "abs_return_ge_3pct_count": int((r.abs() >= .03 - 1e-12).sum()),
        "abs_return_ge_3pct_rate_pct": scalar((r.abs() >= .03 - 1e-12).mean()*100) if len(r) else None,
        "median_range_pct": scalar(d["range"].median()*100),
        "range_p90_pct": scalar(d["range"].quantile(.9)*100),
        "median_abs_gap_pct": scalar(g.abs().median()*100),
        "abs_gap_ge_1pct_count": int((g.abs() >= .01 - 1e-12).sum()),
        "abs_gap_ge_1pct_rate_pct": scalar((g.abs() >= .01 - 1e-12).mean()*100) if len(g) else None,
        "gap_up_ge_1pct_intraday_up": conditional(d, d.gap >= .01 - 1e-12, d.intraday > 0),
        "gap_up_ge_1pct_reference_touched": conditional(d, d.gap >= .01 - 1e-12, d.low <= d.pre_close),
        "gap_down_le_minus1pct_intraday_down": conditional(d, d.gap <= -.01 + 1e-12, d.intraday < 0),
        "gap_down_le_minus1pct_reference_touched": conditional(d, d.gap <= -.01 + 1e-12, d.high >= d.pre_close),
    }


def analyze(panel):
    d = prepare(panel)
    codes = [TARGET, *PEERS]
    if set(d.ts_code.unique()) != set(codes):
        raise ValueError("require target and all four predefined peers")
    dates = [set(d.loc[d.ts_code == c, "trade_date"]) for c in codes]
    if any(x != dates[0] for x in dates[1:]):
        raise ValueError("unaligned peer dates")
    summaries = {}
    table = []
    for code in codes:
        x = d[d.ts_code == code]
        summaries[code] = {"all": metrics(x), "years": {}}
        table.append({"ts_code": code, "period": "all", **{
            k:v for k,v in summaries[code]["all"].items() if not isinstance(v, dict)}})
        for year, y in x.groupby(x.trade_date.dt.year):
            m = metrics(y)
            summaries[code]["years"][str(year)] = m
            table.append({"ts_code": code, "period": str(year), **{
                k:v for k,v in m.items() if not isinstance(v, dict)}})
    wide = d.pivot(index="trade_date", columns="ts_code", values="ret")
    # Equal-weight four-peer return proxy: all four required, never partial means.
    complete = wide[codes].dropna()
    pairs = complete.assign(peer_proxy=complete[list(PEERS)].mean(axis=1))
    def assoc(x):
        t, p = x[TARGET], x.peer_proxy
        if len(x) < 3 or t.std() == 0 or p.std() == 0:
            return {"n": len(x), "correlation": None, "beta": None}
        return {"n": len(x), "correlation": scalar(t.corr(p)),
                "beta": scalar(t.cov(p)/p.var())}
    association = {"all": assoc(pairs), "years": {
        str(y):assoc(x) for y,x in pairs.groupby(pairs.index.year)}}
    missing_dates = sorted(d.loc[d.pre_close.isna(), "trade_date"].dt.strftime("%Y-%m-%d").unique())
    return {
        "schema": "pa-v2-descriptive-1",
        "basis": "official_pre_close_reference_price; not total-return or full adj_factor validation",
        "start": str(d.trade_date.min().date()), "end": str(d.trade_date.max().date()),
        "rows": len(d), "sessions_per_stock": len(dates[0]),
        "missing_pre_close_dates": missing_dates,
        "stocks": summaries, "target_vs_equal_weight_four_peer_proxy": association,
        "limitations": [
            "Retrospective descriptive statistics; no predictive or causal inference.",
            "Reference-touch is an OHLC observation, not executable profit or touch ordering.",
            "No full corporate-action/factor/calendar audit; incomplete history.",
            "Peer proxy is not an official industry or market index.",
            "Small gap-event groups and serial dependence preclude confident win-rate claims.",
        ]}, pd.DataFrame(table)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    root, out = Path(args.input_dir), Path(args.out_dir)
    manifest = json.loads((root/"v14_manifest_sha256.json").read_text(encoding="utf-8"))
    paths = [root/n for n in ("600031_2023_Q4_five_stock_daily.csv",
                             "600031_2024_five_stock_daily.csv",
                             "600031_2025_2026_peer_daily.csv")]
    audits = []
    for p in paths:
        raw = p.read_bytes()
        h = hashlib.sha256(raw).hexdigest()
        if h != manifest[p.name]["sha256"] or len(raw) != manifest[p.name]["bytes"]:
            raise ValueError("input integrity failed: "+p.name)
        audits.append({"file":p.name, "sha256":h, "bytes":len(raw)})
    result, table = analyze(pd.concat([pd.read_csv(p) for p in paths], ignore_index=True))
    result["input_audit"] = audits
    result["archived_snapshot"] = json.loads((root/"current.json").read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    table.to_csv(out/"profile_summary.csv", index=False, encoding="utf-8")
    (out/"profile_results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2,
                                                      allow_nan=False), encoding="utf-8")
    print(json.dumps({"target":result["stocks"][TARGET],
                      "association":result["target_vs_equal_weight_four_peer_proxy"]},
                     ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()

