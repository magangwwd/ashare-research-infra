"""Read-only PA-v2 descriptive trend/chop and peak-to-recovery analysis.

Reference-linked returns (close/pre_close-1) are NOT total returns or executable
profits. Missing pre_close breaks windows and right-censors ongoing drawdowns.
"""
import hashlib
import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

CODES = ("600031.SH", "000157.SZ", "000425.SZ", "600761.SH", "601100.SH")
FILES = ("600031_2023_Q4_five_stock_daily.csv",
         "600031_2024_five_stock_daily.csv", "600031_2025_2026_peer_daily.csv")


def load_verified_zip(path):
    with zipfile.ZipFile(path) as z:
        manifest = json.loads(z.read("v14_manifest_sha256.json"))
        frames, audit = [], []
        for name in FILES:
            raw = z.read(name)
            expected = manifest[name]
            sha = hashlib.sha256(raw).hexdigest()
            if sha != expected["sha256"] or len(raw) != expected["bytes"]:
                raise ValueError("archive input integrity failed: " + name)
            audit.append({"file": name, "sha256": sha, "bytes": len(raw)})
            frames.append(pd.read_csv(io.BytesIO(raw)))
    return pd.concat(frames, ignore_index=True), audit


def prepare(panel):
    if panel.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("duplicate stock/date")
    if set(panel.ts_code.unique()) != set(CODES):
        raise ValueError("target/four-peer coverage mismatch")
    d = panel.copy()
    d["trade_date"] = pd.to_datetime(d.trade_date, errors="raise")
    d = d.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    for col in ("close", "pre_close"):
        d[col] = pd.to_numeric(d[col], errors="raise")
    if (d.close <= 0).any() or ((d.pre_close <= 0) & d.pre_close.notna()).any():
        raise ValueError("invalid price")
    dates = [tuple(d.loc[d.ts_code == c, "trade_date"]) for c in CODES]
    if any(x != dates[0] for x in dates[1:]):
        raise ValueError("peer date misalignment")
    d["log_ret"] = np.log(d.close / d.pre_close)
    d.loc[d.pre_close.isna(), "log_ret"] = np.nan
    return d


def trend(d, windows=(5, 20)):
    d = d.sort_values("trade_date").reset_index(drop=True)
    r = d.log_ret
    valid = r.notna()
    sign = np.sign(r)
    pairs = valid & valid.shift(1, fill_value=False) & (sign != 0) & (sign.shift(1) != 0)
    same = pairs & (sign == sign.shift(1))
    out = {"valid_returns": int(valid.sum()), "sign_pairs": int(pairs.sum()),
           "sign_same": int(same.sum()),
           "sign_same_pct": float(100 * same.sum() / pairs.sum()) if pairs.sum() else None,
           "windows": {}}
    for w in windows:
        enough = valid.rolling(w, min_periods=w).sum() == w
        net = r.rolling(w, min_periods=w).sum().where(enough)
        path = r.abs().rolling(w, min_periods=w).sum().where(enough)
        efficiency = (net.abs() / path).where(path > 0)
        out["windows"][str(w)] = {
            "n": int(efficiency.notna().sum()),
            "median_efficiency": float(efficiency.median()) if efficiency.notna().any() else None,
            "p25_efficiency": float(efficiency.quantile(.25)) if efficiency.notna().any() else None,
            "p75_efficiency": float(efficiency.quantile(.75)) if efficiency.notna().any() else None,
            "median_net_reference_pct": float(np.expm1(net).median() * 100)
            if net.notna().any() else None}
    return out


def episodes(d):
    """Peak/trough are retrospective labels, never known before their dates."""
    d = d.sort_values("trade_date").reset_index(drop=True)
    events, active = [], None
    value = high = 1.0
    peak_i = peak_date = None
    for i, row in d.iterrows():
        r, day = row.log_ret, row.trade_date.strftime("%Y-%m-%d")
        if pd.isna(r):
            if active is not None:
                active.update(status="censored_data_gap",
                              last_date=d.iloc[i-1].trade_date.strftime("%Y-%m-%d"),
                              observed_sessions=i-active["peak_i"]-1)
                events.append(active)
            active = None
            value = high = 1.0
            peak_i = peak_date = None
            continue
        value *= float(np.exp(r))
        if peak_i is None:
            high, peak_i, peak_date = value, i, day
            continue
        if value >= high - 1e-10:
            if active is not None:
                active.update(status="recovered", recovered_date=day,
                              sessions_peak_to_recover=i-active["peak_i"],
                              sessions_trough_to_recover=i-active["trough_i"],
                              observed_sessions=i-active["peak_i"])
                events.append(active)
                active = None
            high, peak_i, peak_date = value, i, day
        else:
            depth = 100 * (value / high - 1)
            if active is None:
                active = {"peak_date": peak_date, "start_date": day, "peak_i": peak_i,
                          "trough_date": day, "trough_i": i, "max_drawdown_pct": depth}
            elif depth < active["max_drawdown_pct"]:
                active.update(trough_date=day, trough_i=i, max_drawdown_pct=depth)
    if active is not None:
        active.update(status="censored_sample_end",
                      last_date=d.iloc[-1].trade_date.strftime("%Y-%m-%d"),
                      observed_sessions=len(d)-1-active["peak_i"])
        events.append(active)
    for e in events:
        del e["peak_i"]
        del e["trough_i"]
    return events


def episode_summary(events):
    recovered = [e for e in events if e["status"] == "recovered"]
    censored = [e for e in events if e["status"] != "recovered"]
    median = lambda x: float(np.median(x)) if x else None
    return {"episodes": len(events), "recovered": len(recovered),
            "censored": len(censored),
            "censored_data_gap": sum(e["status"] == "censored_data_gap" for e in censored),
            "censored_sample_end": sum(e["status"] == "censored_sample_end" for e in censored),
            "median_max_depth_pct_all": median([e["max_drawdown_pct"] for e in events]),
            "worst_depth_pct_all": min((e["max_drawdown_pct"] for e in events), default=None),
            "median_sessions_peak_to_recover_recovered_only":
                median([e["sessions_peak_to_recover"] for e in recovered]),
            "median_sessions_trough_to_recover_recovered_only":
                median([e["sessions_trough_to_recover"] for e in recovered]),
            "max_observed_sessions_censored":
                max((e["observed_sessions"] for e in censored), default=None)}


def analyze(panel):
    d = prepare(panel)
    stocks, events = {}, []
    for code in CODES:
        s = d[d.ts_code == code].copy()
        ep = episodes(s)
        events.extend([{"ts_code": code, **e} for e in ep])
        stocks[code] = {"trend_all": trend(s),
                       "trend_by_year": {str(y): trend(g)
                                         for y, g in s.groupby(s.trade_date.dt.year)},
                       "drawdown": episode_summary(ep)}
    return {"basis": "official close/pre_close reference-linked log returns; NOT total return",
            "range": [str(d.trade_date.min().date()), str(d.trade_date.max().date())],
            "rows": len(d), "stocks": stocks,
            "warnings": ["E1 retrospective, overlapping windows not independent",
                         "Unrecovered episodes retained as right-censored",
                         "Peak/trough labels are hindsight, not signals",
                         "No full corporate-action or tradability audit"]}, events


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--zip", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    panel, audit = load_verified_zip(a.zip)
    result, events = analyze(panel)
    result["input_audit"] = audit
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "trend_repair_results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    pd.DataFrame(events).to_csv(out / "drawdown_episodes.csv", index=False)


if __name__ == "__main__":
    main()
