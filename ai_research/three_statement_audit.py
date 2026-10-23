"""Fail-closed, read-only three-statement coverage audit for PIT research.
Input: JSON array of records with ts_code, dataset, report_period, available_date,
and optionally ann_date, actual_ann_date, report_type, comp_type.
This tool checks coverage, not accounting values or Tushare entitlements.
"""
import argparse
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

DATASETS = ("income", "balancesheet", "cashflow")
DEFAULT_SYMBOLS = ("600351.SH", "002107.SZ", "600641.SH", "002568.SZ", "002296.SZ")

def audit(records, symbols, periods, as_of):
    as_of = date.fromisoformat(as_of)
    by_key = defaultdict(list)
    rejected = []
    for index, row in enumerate(records):
        try:
            symbol, ds = row["ts_code"], row["dataset"]
            if symbol not in symbols or ds not in DATASETS:
                continue
            period = date.fromisoformat(str(row["report_period"])[:10])
            if period.isoformat() not in periods:
                continue
            dates = [date.fromisoformat(str(row[k])[:10]) for k in
                     ("ann_date", "actual_ann_date", "available_date") if row.get(k)]
            if not dates:
                rejected.append({"row": index, "reason": "NO_ANNOUNCEMENT_DATE"})
                continue
            effective = max(dates)
            if effective >= as_of:
                rejected.append({"row": index, "reason": "NOT_PIT_AVAILABLE"})
                continue
            by_key[(symbol, period.isoformat(), ds)].append((effective, row))
        except (KeyError, ValueError, TypeError):
            rejected.append({"row": index, "reason": "INVALID_TEMPORAL_FIELDS"})
    results = {}
    for symbol in symbols:
        results[symbol] = {}
        for period in periods:
            detail = {}
            for ds in DATASETS:
                matches = by_key[(symbol, period, ds)]
                if not matches:
                    detail[ds] = "MISSING"
                else:
                    latest = max(m[0] for m in matches)
                    latest_rows = [m[1] for m in matches if m[0] == latest]
                    detail[ds] = "AMBIGUOUS" if len(latest_rows) != 1 else "PRESENT"
            results[symbol][period] = detail
    complete = all(v == "PRESENT" for periods_map in results.values()
                   for ds_map in periods_map.values() for v in ds_map.values())
    return {"status": "PASS" if complete else "FAIL_CLOSED",
            "authorization": "PAPER_ONLY_NO_ENTRY_AUTHORIZATION",
            "as_of": as_of.isoformat(), "symbols": results,
            "rejected_rows": rejected,
            "scope": "three-statement coverage and point-in-time gate only",
            "accounting_reconciliation": "NOT_TESTED",
            "drive_publication": "NOT_TESTED"}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--as-of", required=True)
    parser.add_argument("--period", action="append", required=True)
    parser.add_argument("--symbol", action="append")
    args = parser.parse_args()
    rows = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        parser.error("input must be a JSON array")
    result = audit(rows, args.symbol or DEFAULT_SYMBOLS, args.period, args.as_of)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(result["status"])
    return 0 if result["status"] == "PASS" else 2

if __name__ == "__main__":
    raise SystemExit(main())
