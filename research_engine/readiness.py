"""Backtest-readiness gate based on recent consecutive core sessions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from data_pipeline.manifest import atomic_json

from .data import CleanResearchData


LEVELS = (
    ("engineering", 60),
    ("exploratory", 250),
    ("research", 750),
    ("robustness", 1250),
    ("stress_test", 2000),
)


def consecutive_core_sessions(data: CleanResearchData) -> tuple[int, object | None]:
    core_dates = []
    for name in ("daily", "adj_factor", "daily_basic"):
        frame = data.load(name)
        core_dates.append(set(frame["trade_date"].dropna().tolist()))
    common = set.intersection(*core_dates) if core_dates else set()
    if not common:
        return 0, None

    calendar = data.calendar()
    open_dates = sorted(set(calendar.loc[
        calendar["exchange"].isin(["SSE", "SZSE"]) & calendar["is_open"].eq(1),
        "calendar_date",
    ].tolist()))
    latest = max(common)
    relevant = [d for d in open_dates if d <= latest]
    count = 0
    for day in reversed(relevant):
        if day not in common:
            break
        count += 1
    return count, latest


def readiness_report(data: CleanResearchData) -> dict:
    count, latest = consecutive_core_sessions(data)
    levels = {
        name: {"threshold": threshold, "ready": count >= threshold, "remaining": max(0, threshold - count)}
        for name, threshold in LEVELS
    }
    return {
        "schema_version": "1.0.0",
        "accepted_snapshot": True,
        "as_of": data.acceptance.get("as_of"),
        "latest_common_core_session": str(latest) if latest else None,
        "recent_consecutive_core_sessions": count,
        "levels": levels,
        "interpretation": "readiness controls research maturity; it does not assert strategy profitability",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    data = CleanResearchData(args.root)
    report = readiness_report(data)
    if args.write:
        atomic_json(Path(args.root) / "metadata/backtest_readiness.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
