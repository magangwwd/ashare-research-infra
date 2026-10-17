#!/usr/bin/env python3
"""Historical validation for the BTC regime -> strategy relationship.

This module is deliberately conservative: it does not optimize thresholds on
the full history. It reports regime coverage, persistence, forward BTC
behaviour, and (when supplied) conditional candidate-strategy performance.
A later version can promote a mapping only after walk-forward OOS evidence.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path


def read_csv(path):
    with Path(path).open(newline="", encoding="utf8") as f:
        return list(csv.DictReader(f))


def forward_stats(rows, states, horizon=20):
    close = [float(r["close"]) for r in rows]
    dates = [r["date"] for r in rows]
    regime = {r["date"]: r["confirmed_regime"] for r in states}
    buckets = defaultdict(list)

    for i in range(len(rows) - horizon):
        d = dates[i]
        g = regime.get(d)
        if g is None:
            continue
        r = close[i + horizon] / close[i] - 1.0
        path = close[i + 1:i + horizon + 1]
        mae = min(path) / close[i] - 1.0
        buckets[g].append((r, mae))

    out = []
    for g, vals in sorted(buckets.items()):
        rs = [x[0] for x in vals]
        maes = [x[1] for x in vals]
        out.append({
            "regime": g,
            "observations": len(vals),
            "forward20_mean": statistics.mean(rs),
            "forward20_median": statistics.median(rs),
            "forward20_loss_rate": sum(x < 0 for x in rs) / len(rs),
            "forward20_mae_mean": statistics.mean(maes),
            "forward20_mae_worst": min(maes),
        })
    return out


def regime_coverage(states):
    counts = defaultdict(int)
    episodes = defaultdict(int)
    durations = defaultdict(list)
    prev = None
    run = 0
    for row in states:
        g = row["confirmed_regime"]
        counts[g] += 1
        if g != prev:
            if prev is not None:
                durations[prev].append(run)
            episodes[g] += 1
            prev = g
            run = 1
        else:
            run += 1
    if prev is not None:
        durations[prev].append(run)

    out = []
    for g in sorted(counts):
        ds = durations[g]
        out.append({
            "regime": g,
            "observations": counts[g],
            "episodes": episodes[g],
            "median_episode_days": statistics.median(ds),
            "max_episode_days": max(ds),
        })
    return out


def equity_returns(path):
    if not path:
        return {}
    rows = read_csv(path)
    nav = {r["date"]: float(r["nav"]) for r in rows}
    dates = sorted(nav)
    return {dates[i]: math.log(nav[dates[i]] / nav[dates[i-1]])
            for i in range(1, len(dates)) if nav[dates[i-1]] > 0 and nav[dates[i]] > 0}


def conditional_strategy_matrix(states, strategy_paths):
    returns = {name: equity_returns(path) for name, path in strategy_paths.items() if path}
    regime = {r["date"]: r["confirmed_regime"] for r in states}
    # T close-derived regime can only explain T+1 returns.
    ordered = sorted({d for series in returns.values() for d in series})
    predecessor = {ordered[i]: ordered[i-1] for i in range(1, len(ordered))}
    buckets = defaultdict(lambda: defaultdict(list))
    for name, series in returns.items():
        for d, r in series.items():
            g = regime.get(predecessor.get(d))
            if g:
                buckets[g][name].append(r)

    out = []
    for g in sorted(buckets):
        for name in sorted(buckets[g]):
            vals = buckets[g][name]
            if not vals:
                continue
            out.append({
                "regime": g,
                "strategy": name,
                "observations": len(vals),
                "mean_daily_log_return": statistics.mean(vals),
                "median_daily_log_return": statistics.median(vals),
                "daily_loss_rate": sum(x < 0 for x in vals) / len(vals),
            })
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--daily", required=True)
    p.add_argument("--regime-csv", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--v3-equity")
    p.add_argument("--hmg-equity")
    args = p.parse_args()

    rows = read_csv(args.daily)
    states = read_csv(args.regime_csv)
    coverage = regime_coverage(states)
    forward = forward_stats(rows, states, horizon=20)
    matrix = conditional_strategy_matrix(
        states,
        {"V3": args.v3_equity, "HMG": args.hmg_equity},
    )

    min_obs = 120
    weak = [x["regime"] for x in coverage if x["observations"] < min_obs]
    summary = {
        "research_only": True,
        "data_start": rows[0]["date"],
        "data_end": rows[-1]["date"],
        "regime_observations": sum(x["observations"] for x in coverage),
        "weak_regimes_under_120_observations": weak,
        "historical_evidence_status": "INSUFFICIENT" if weak else "DESCRIPTIVE_ONLY_NOT_WALK_FORWARD",
        "important_note": "This report is evidence for strategy selection, not proof of future profitability.",
    }

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "validation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf8"
    )

    for name, data in {
        "regime_coverage.csv": coverage,
        "forward_regime_behavior.csv": forward,
        "conditional_strategy_matrix.csv": matrix,
    }.items():
        with (out / name).open("w", newline="", encoding="utf8") as f:
            if data:
                w = csv.DictWriter(f, fieldnames=list(data[0]))
                w.writeheader()
                w.writerows(data)

    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
