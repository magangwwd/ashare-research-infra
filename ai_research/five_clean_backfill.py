"""Isolated 5-stock Tushare 2026Q1/H1 source-to-Clean backfill.

This collector deliberately does not change the existing Task01 manifest, existing
Clean stage, strategy signals, or any brokerage actions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import date, datetime, timezone
from pathlib import Path

SYMBOLS = ("600351.SH", "002107.SZ", "600641.SH", "002568.SZ", "002296.SZ")
PERIODS = ("20260331", "20260630")
DATASETS = ("income", "balancesheet", "cashflow", "fina_indicator")
REQUIRED_NUMBERS = {
    "income": ("total_revenue", "n_income_attr_p", "operate_profit"),
    "balancesheet": ("total_assets", "total_liab", "total_cur_assets", "total_cur_liab"),
    "cashflow": ("n_cashflow_act",),
    "fina_indicator": ("roe", "debt_to_assets", "current_ratio"),
}
AUTHORIZATION = "PAPER_ONLY_NO_ENTRY_AUTHORIZATION"


def select_report(frame, symbol: str, period: str, dataset: str):
    """Select dated, consolidated, non-ambiguous issuer data, not a substitute."""
    import pandas as pd
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError("NO_ROWS")
    for name in ("ts_code", "end_date", "ann_date"):
        if name not in frame:
            raise ValueError("MISSING_COLUMN:" + name)
    rows = frame.loc[
        frame["ts_code"].astype(str).eq(symbol) &
        frame["end_date"].astype(str).eq(period)
    ].copy()
    if dataset in ("income", "balancesheet", "cashflow"):
        if "report_type" not in rows:
            raise ValueError("MISSING_COLUMN:report_type")
        # Tushare report_type=1 denotes consolidated statements.
        rows = rows.loc[rows["report_type"].astype(str).isin(("1", "1.0"))]
        if "comp_type" in rows:
            rows = rows.loc[rows["comp_type"].isna() |
                            rows["comp_type"].astype(str).isin(("1", "1.0"))]
    dates = rows["ann_date"].astype(str)
    rows = rows.loc[dates.str.fullmatch(r"\d{8}")].copy()
    for name in REQUIRED_NUMBERS[dataset]:
        if name not in rows:
            raise ValueError("MISSING_COLUMN:" + name)
        rows[name] = pd.to_numeric(rows[name], errors="coerce")
        rows = rows.loc[rows[name].notna()]
    if rows.empty:
        raise ValueError("NO_COMPLETE_CONSOLIDATED_REPORT")
    # Never accept future-issued corrections as historical inputs.
    stamp = date.today().strftime("%Y%m%d")
    rows = rows.loc[rows["ann_date"].astype(str).le(stamp)]
    if rows.empty:
        raise ValueError("FUTURE_DISCLOSURE")
    order = [n for n in ("ann_date", "f_ann_date", "update_flag") if n in rows]
    rows = rows.sort_values(order, kind="stable", na_position="first")
    chosen = rows.iloc[[-1]].copy()
    if len(rows) > 1:
        same = rows
        final = chosen.iloc[0]
        for name in order:
            same = same.loc[same[name].fillna("__NULL__").astype(str) ==
                            str(final[name] if pd.notna(final[name]) else "__NULL__")]
        if len(same) > 1:
            raise ValueError("AMBIGUOUS_DUPLICATE_REPORT")
    return chosen


def collect(root: Path, client, throttle: float = 0):
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq
    from data_pipeline.clean import normalize, arrow_schema

    root.mkdir(parents=True, exist_ok=True)
    captured = datetime.now(timezone.utc)
    audit = {
        "status": "INCOMPLETE", "scope": "FIVE_STOCK_TWO_PERIOD_FOUR_DATASET",
        "provider": "TUSHARE_PRO", "captured_utc": captured.isoformat(),
        "authorization": AUTHORIZATION, "expected": 40, "valid": 0,
        "issues": [], "records": [], "files": {},
        "does_not_certify_full_market_or_historical_coverage": True,
    }
    for dataset in DATASETS:
        for period in PERIODS:
            accepted = []
            for symbol in SYMBOLS:
                try:
                    original = client.query(dataset, ts_code=symbol, period=period)
                    selected = select_report(original, symbol, period, dataset)
                    cleaned = normalize(dataset, selected)
                    if (len(cleaned) != 1 or cleaned["ts_code"].iloc[0] != symbol or
                            cleaned["report_period"].iloc[0].strftime("%Y%m%d") != period or
                            pd.isna(cleaned["ann_date"].iloc[0])):
                        raise ValueError("INVALID_NORMALIZED_FINANCIAL")
                    accepted.append(cleaned)
                    audit["valid"] += 1
                    audit["records"].append({
                        "dataset": dataset, "period": period,
                        "symbol": symbol, "ann_date": cleaned["ann_date"].iloc[0].isoformat(),
                        "status": "VALID",
                    })
                except Exception as error:
                    reason = str(error)
                    token = os.environ.get("TUSHARE_TOKEN", "")
                    if token:
                        reason = reason.replace(token, "[REDACTED]")
                    audit["issues"].append({"dataset": dataset, "period": period,
                                            "symbol": symbol, "reason": reason[:400]})
                if throttle > 0:
                    time.sleep(throttle)
            if len(accepted) == len(SYMBOLS):
                combined = pd.concat(accepted, ignore_index=True)
                file = root / (dataset + "_" + period + ".parquet")
                pq.write_table(pa.Table.from_pandas(combined, schema=arrow_schema(dataset),
                                                     preserve_index=False), file, compression="zstd")
                audit["files"][file.name] = {
                    "rows": len(combined),
                    "bytes": file.stat().st_size,
                    "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                }
    complete = (audit["valid"] == 40 and len(audit["files"]) == 8 and not audit["issues"])
    audit["status"] = "COMPLETE" if complete else "INCOMPLETE"
    (root / "audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
    if complete:
        pointer = {
            "version": "five-clean-" + os.environ.get("GITHUB_RUN_ID", "local"),
            "validated": True, "target_scope_complete": True,
            "snapshot_as_of": captured.date().isoformat(), "published_at": captured.isoformat(),
            "target_symbols": SYMBOLS, "report_periods": PERIODS, "datasets": DATASETS,
            "critical_history_complete": False, "full_requested_core_range_complete": False,
            "authorization": AUTHORIZATION,
        }
        (root / "targeted_current.json").write_text(
            json.dumps(pointer, ensure_ascii=False, indent=2), encoding="utf-8")
        (root / "targeted_acceptance.json").write_text(json.dumps({
            "passed": True, "target_scope_complete": True, "groups": 40,
            "partitions": audit["files"], "authorization": AUTHORIZATION,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": audit["status"], "valid": audit["valid"],
                      "expected": 40, "partitions": len(audit["files"]),
                      "issues": len(audit["issues"])}, ensure_ascii=False))
    return complete


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--throttle", type=float, default=0.7)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        (args.out / "audit.json").write_text(json.dumps({
            "status": "INCOMPLETE", "provider": "TUSHARE_PRO",
            "issues": [{"reason": "TUSHARE_TOKEN_MISSING"}],
            "authorization": AUTHORIZATION,
        }), encoding="utf-8")
        return 2
    try:
        import tushare as ts
        ts.set_token(token)
        client = ts.pro_api(token)
        return 0 if collect(args.out, client, args.throttle) else 2
    except Exception as exc:
        redacted = str(exc).replace(token, "[REDACTED]")
        (args.out / "audit.json").write_text(json.dumps({
            "status": "INCOMPLETE", "provider": "TUSHARE_PRO",
            "issues": [{"reason": ("PIPELINE_EXCEPTION:" + redacted)[:400]}],
            "authorization": AUTHORIZATION,
        }), encoding="utf-8")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
