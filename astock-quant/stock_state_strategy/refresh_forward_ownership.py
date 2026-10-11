"""Build an as-of ownership disclosure timeline for a forward snapshot.

The historical raw downloads are kept unchanged.  The script downloads only
new holder-number announcements, checks the same issuers for newly published
top-10 floating-holder tables, and combines those rows with the historical raw
tables into an isolated point-in-time timeline.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd
import pyarrow.parquet as pq
import tushare as ts

from ownership_pipeline import (
    ROOT,
    build_composition_updates,
    build_event_timeline,
    build_holder_updates,
    load_config,
    read_raw_parts,
    resolve,
)


def call_with_retry(
    fn: Callable[..., pd.DataFrame],
    kwargs: dict,
    attempts: int,
    min_interval: float,
) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        started = time.monotonic()
        try:
            frame = fn(**kwargs)
            elapsed = time.monotonic() - started
            if elapsed < min_interval:
                time.sleep(min_interval - elapsed)
            return frame
        except Exception as exc:  # upstream errors are not consistently typed
            last_error = exc
            if attempt < attempts:
                time.sleep(min(30.0, 1.5**attempt))
    assert last_error is not None
    raise last_error


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", type=Path, default=ROOT / "configs" / "ownership_state.json"
    )
    parser.add_argument("--start-ann-date", required=True)
    parser.add_argument("--end-ann-date", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--min-interval", type=float, default=None)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = load_config(args.config.resolve())
    start = pd.Timestamp(args.start_ann_date).normalize()
    end = pd.Timestamp(args.end_ann_date).normalize()
    if start > end:
        raise ValueError("start announcement date is after end announcement date")

    token = os.environ.get("TUSHARE_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TUSHARE_TOKEN is missing or empty")

    market_root = resolve(config["market_data_root"])
    raw_root = resolve(config["raw_data_dir"])
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    symbols = set(
        pq.read_table(
            market_root / "instruments.parquet", columns=["vendor_symbol"]
        )
        .to_pandas()["vendor_symbol"]
        .dropna()
        .astype(str)
    )
    attempts = int(config["download_attempts"])
    min_interval = (
        float(args.min_interval)
        if args.min_interval is not None
        else float(config["download_min_interval_seconds"])
    )
    pro = ts.pro_api(token)
    start_text = start.strftime("%Y%m%d")
    end_text = end.strftime("%Y%m%d")

    holder_increment = call_with_retry(
        pro.stk_holdernumber,
        {"start_date": start_text, "end_date": end_text},
        attempts,
        min_interval,
    )
    holder_increment = holder_increment.reindex(
        columns=["ts_code", "ann_date", "end_date", "holder_num"]
    )
    holder_increment = holder_increment[
        holder_increment["ts_code"].astype(str).isin(symbols)
    ].copy()
    holder_increment["ann_date"] = pd.to_datetime(
        holder_increment["ann_date"], errors="coerce"
    )
    holder_increment = holder_increment[
        holder_increment["ann_date"].between(start, end)
    ].copy()
    holder_increment["ann_date"] = holder_increment["ann_date"].dt.strftime(
        "%Y%m%d"
    )
    holder_increment["queried_symbol"] = holder_increment["ts_code"]
    candidate_symbols = sorted(holder_increment["ts_code"].dropna().unique())
    holder_increment.to_parquet(
        output_dir / "holder_number_increment.parquet", index=False
    )

    top_columns = [
        "ts_code",
        "ann_date",
        "end_date",
        "holder_name",
        "hold_amount",
        "hold_ratio",
        "hold_float_ratio",
        "hold_change",
        "holder_type",
    ]
    top_parts: list[pd.DataFrame] = []
    failures: list[dict[str, str]] = []
    # A late-August disclosure is normally a half-year report.  A broad report
    # period range also captures corrections or delayed first-quarter tables;
    # the point-in-time filter below remains based strictly on ann_date.
    report_start = pd.Timestamp(year=end.year, month=1, day=1).strftime("%Y%m%d")
    report_end = end_text
    total = len(candidate_symbols)
    for number, symbol in enumerate(candidate_symbols, start=1):
        try:
            frame = call_with_retry(
                pro.top10_floatholders,
                {
                    "ts_code": symbol,
                    "start_date": report_start,
                    "end_date": report_end,
                },
                attempts,
                min_interval,
            )
            if not frame.empty:
                frame = frame.reindex(columns=top_columns).copy()
                ann = pd.to_datetime(frame["ann_date"], errors="coerce")
                frame = frame[ann.between(start, end)].copy()
                if not frame.empty:
                    frame["queried_symbol"] = symbol
                    top_parts.append(frame)
        except Exception as exc:
            failures.append(
                {
                    "symbol": symbol,
                    "error_type": type(exc).__name__,
                    "message": str(exc)[:500],
                }
            )
        if number % 100 == 0 or number == total:
            print(
                f"top10_float: {number}/{total}, "
                f"new_parts={len(top_parts)}, failures={len(failures)}",
                flush=True,
            )
    top_increment = (
        pd.concat(top_parts, ignore_index=True)
        if top_parts
        else pd.DataFrame(columns=[*top_columns, "queried_symbol"])
    )
    top_increment.to_parquet(output_dir / "top10_float_increment.parquet", index=False)

    holder_raw = pd.concat(
        [read_raw_parts(raw_root, "holder_number"), holder_increment],
        ignore_index=True,
    ).drop_duplicates()
    top_raw = pd.concat(
        [read_raw_parts(raw_root, "top10_float"), top_increment],
        ignore_index=True,
    ).drop_duplicates()
    holder = build_holder_updates(holder_raw)
    composition, detail = build_composition_updates(top_raw)
    events = build_event_timeline(holder, composition)
    events = events[
        pd.to_datetime(events["ann_date"], errors="coerce").le(end)
    ].copy()
    events = events.rename(columns={"ts_code": "vendor_symbol"})
    events.to_parquet(output_dir / "ownership_events.parquet", index=False)
    detail.to_parquet(output_dir / "classified_top10_float.parquet", index=False)

    summary = {
        "as_of_date": str(end.date()),
        "increment_start_date": str(start.date()),
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "main_board_holder_increment_rows": int(len(holder_increment)),
        "main_board_holder_increment_symbols": int(len(candidate_symbols)),
        "top10_increment_rows": int(len(top_increment)),
        "top10_increment_symbols": int(top_increment["ts_code"].nunique())
        if len(top_increment)
        else 0,
        "top10_query_failures": failures,
        "timeline_rows": int(len(events)),
        "timeline_symbols": int(events["vendor_symbol"].nunique()),
        "timeline_announcement_end": str(pd.to_datetime(events["ann_date"]).max().date()),
        "method_note": (
            "Top-10 refresh candidates are issuers with a holder-number "
            "announcement in the same interval; announcements are filtered "
            "by ann_date after download."
        ),
    }
    (output_dir / "refresh_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
