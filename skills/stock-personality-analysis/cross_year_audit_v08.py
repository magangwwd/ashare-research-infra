#!/usr/bin/env python3
"""Read-only cross-year warm-up audit for frozen 600031 patterns.

Do not reset 20-day rolling features on January 1. This is a retrospective
correction, NOT a prospective holdout or a tradable strategy.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from stock_personality import load_panel
from execution_backtest import trades
from backward_stress_v06 import KEYS, PEERS, TARGET


def check_panel(panel):
    if sorted(panel.ts_code.unique()) != sorted([TARGET, *PEERS]):
        raise ValueError("Frozen five-stock panel required")
    if panel.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("Duplicate ts_code/date")
    calendars = [tuple(g.trade_date.sort_values()) for _, g in panel.groupby("ts_code")]
    if any(c != calendars[0] for c in calendars[1:]):
        raise ValueError("Peer trading calendars are not aligned")
    return pd.Index(calendars[0])


def audit(paths):
    frames = [load_panel(p) for p in paths]
    panel = pd.concat(frames, ignore_index=True)
    if panel.duplicated(["ts_code", "trade_date"]).any():
        raise ValueError("Overlapping source files; no silent deduplication")
    panel = panel.sort_values(["ts_code", "trade_date"]).reset_index(drop=True)
    dates = check_panel(panel)
    target = panel[panel.ts_code == TARGET].reset_index(drop=True)
    opens = panel.pivot(index="trade_date", columns="ts_code", values="open").sort_index()
    years = sorted(target.trade_date.dt.year.unique())
    result = {
        "scope": "Retrospective cross-year warm-up correction, NOT untouched holdout",
        "target": TARGET, "peers": PEERS,
        "source_csv_sha256": {Path(p).name: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths},
        "start": str(dates.min().date()), "end": str(dates.max().date()),
        "sessions": len(dates), "rows": len(panel),
        "years": {str(y): {"sessions": int((target.trade_date.dt.year == y).sum()),
                            "pre_close_nulls_five_stocks": int(panel.loc[panel.trade_date.dt.year == y, "pre_close"].isna().sum())}
                  for y in years},
        "results": []
    }
    for signal, h in KEYS:
        continuous = trades(target, signal, h)
        for year in years:
            single = target[target.trade_date.dt.year == year].reset_index(drop=True)
            reset = trades(single, signal, h)
            current = [t for t in continuous if t["year"] == year]
            reset_days = {t["signal_date"] for t in reset}
            current_days = {t["signal_date"] for t in current}
            matched = []
            for t in current:
                entry, exit_ = pd.Timestamp(t["entry_date"]), pd.Timestamp(t["exit_date"])
                peer_gross = 100 * (opens.loc[exit_, PEERS] / opens.loc[entry, PEERS] - 1).mean()
                matched.append({**t, "peer_gross_pct": round(float(peer_gross), 4),
                                "target_minus_peer_gross_pp": round(float(t["gross_return_pct"] - peer_gross), 4)})
            def mean(k):
                return round(float(np.mean([v[k] for v in matched])), 3) if matched else None
            result["results"].append({
                "signal": signal, "hold_sessions": h, "year": int(year),
                "year_reset_trade_n": len(reset), "continuous_warmup_trade_n": len(current),
                "added_by_prior_year_warmup": sorted(current_days - reset_days),
                "lost_from_year_reset": sorted(reset_days - current_days),
                "mean_target_net_pct": mean("net_return_pct"),
                "mean_target_gross_pct": mean("gross_return_pct"),
                "mean_peer_gross_pct": mean("peer_gross_pct"),
                "mean_target_minus_peer_gross_pp": mean("target_minus_peer_gross_pp"),
                "underperform_peer_n": sum(t["target_minus_peer_gross_pp"] < 0 for t in matched),
                "possible_ex_action_n": sum(abs(t["ex_factor_during_holding"] - 1) > 1e-6 for t in matched),
                "trades": matched
            })
    result["limitations"] = [
        "2023 Jan-Aug missing; 2023 Sep-Dec is partial-year only.",
        "All historical years were explored; no blind holdout.",
        "Gross peer-relative return is not net alpha.",
        "No verified dividends, limit/suspension fills, minimum commission or slippage.",
        "2026-09-30 published pre_close values are missing for the five stocks."
    ]
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--daily", nargs=3, required=True, help="2023Q4, 2024, 2025-26 archived CSVs")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    result = audit(a.daily)
    Path(a.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("AUDIT", result["sessions"], "sessions", result["rows"], "rows")


if __name__ == "__main__":
    main()
