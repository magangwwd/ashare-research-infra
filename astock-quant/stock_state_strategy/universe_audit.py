"""Fail-closed audit of the refreshed A-share main-board stock list.

The full L/D/P universe is preserved; delisting changes a stock's status,
not whether its historical series continues to exist. No trade signals.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

REQUIRED = {"instrument_id", "vendor_symbol", "exchange", "listing_status", "list_date", "delist_date", "as_of_date"}
STATES = {"LISTED", "DELISTED", "PAUSED_LISTING"}
EXCHANGES = {"SSE", "SZSE"}


def clean(value: object) -> str:
    if value is None:
        return ""
    result = str(value).strip()
    return "" if result in {"nan", "NaT", "<NA>", "None"} else result


def iso_date(value: object) -> str:
    result = clean(value)
    if len(result) == 8 and result.isdigit():
        return result[:4] + "-" + result[4:6] + "-" + result[6:]
    return result


def index_rows(rows: list[dict], label: str) -> dict[str, dict]:
    indexed = {}
    for row in rows:
        missing = REQUIRED - set(row)
        if missing:
            raise ValueError(f"{label}: missing fields: {sorted(missing)}")
        symbol = clean(row["vendor_symbol"])
        if not symbol or symbol in indexed or not clean(row["instrument_id"]):
            raise ValueError(f"{label}: duplicate or empty symbol: {symbol!r}")
        if clean(row["exchange"]) not in EXCHANGES or clean(row["listing_status"]) not in STATES:
            raise ValueError(f"{label}: unexpected exchange or listing status: {symbol}")
        if row["listing_status"] == "LISTED" and not iso_date(row["list_date"]):
            raise ValueError(f"{label}: listed stock without listing date: {symbol}")
        indexed[symbol] = {key: iso_date(row[key]) if key.endswith("_date") else clean(row[key])
                           for key in REQUIRED}
    if not indexed:
        raise ValueError(f"{label}: empty stock universe")
    return indexed


def audit_universe(previous: list[dict] | None, current: list[dict], source: list[dict]) -> dict:
    latest = index_rows(current, "current")
    raw = {}
    for row in source:
        symbol = clean(row.get("ts_code"))
        if not symbol or symbol in raw:
            raise ValueError(f"source: empty or duplicate stock: {symbol!r}")
        if clean(row.get("market")) != "主板" or clean(row.get("exchange")) not in EXCHANGES:
            raise ValueError(f"source: non-mainboard stock: {symbol}")
        if clean(row.get("list_status")) not in {"L", "D", "P"}:
            raise ValueError(f"source: invalid status: {symbol}")
        raw[symbol] = row
    if set(latest) != set(raw):
        raise ValueError("source/canonical stock universe mismatch")
    dates = {r["as_of_date"] for r in latest.values()}
    if len(dates) != 1 or not next(iter(dates)):
        raise ValueError("stock universe has mixed or empty as_of_date")
    date = next(iter(dates))
    status_map = {"L": "LISTED", "D": "DELISTED", "P": "PAUSED_LISTING"}
    for symbol, new in latest.items():
        original = raw[symbol]
        if (new["listing_status"] != status_map[clean(original["list_status"])]
                or new["exchange"] != clean(original["exchange"])
                or new["as_of_date"] != iso_date(original.get("query_trade_date"))):
            raise ValueError(f"stock metadata mismatch: {symbol}")
    old = index_rows(previous, "previous") if previous is not None else {}
    missing = sorted(set(old) - set(latest))
    if missing:
        raise ValueError(f"previously known stocks vanished ({len(missing)}): {missing[:10]}")
    if old and any(row["as_of_date"] > date for row in old.values()):
        raise ValueError("new universe snapshot is older than previous")
    id_changes = [s for s in set(old) & set(latest)
                  if old[s]["instrument_id"] != latest[s]["instrument_id"]]
    if id_changes:
        raise ValueError(f"stable instrument identities changed: {sorted(id_changes)[:10]}")
    return {
        "status": "VALIDATED",
        "as_of_date": date,
        "previous_count": len(old) if previous is not None else None,
        "current_count": len(latest),
        "listed_count": sum(r["listing_status"] == "LISTED" for r in latest.values()),
        "delisted_count": sum(r["listing_status"] == "DELISTED" for r in latest.values()),
        "paused_count": sum(r["listing_status"] == "PAUSED_LISTING" for r in latest.values()),
        "new_symbols": sorted(set(latest) - set(old)),
        "status_changes": [{"symbol": s, "before": old[s]["listing_status"], "after": latest[s]["listing_status"]}
                           for s in sorted(set(old) & set(latest))
                           if old[s]["listing_status"] != latest[s]["listing_status"]],
        "missing_listing_dates": sorted(s for s, r in latest.items() if not r["list_date"]),
        "missing_delisting_dates": sorted(s for s, r in latest.items()
                                         if r["listing_status"] == "DELISTED" and not r["delist_date"]),
        "removed_symbols": [],
        "source": "Tushare stock_basic L+D+P, market=主板, SSE/SZSE",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--current", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    import pandas as pd
    old = pd.read_parquet(args.previous).to_dict("records") if args.previous else None
    latest = pd.read_parquet(args.current).to_dict("records")
    source = pd.read_parquet(args.source).to_dict("records")
    report = audit_universe(old, latest, source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(".json.tmp")
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(args.output)
    print(f"SUCCESS stock universe: {report['current_count']} stocks, "
          f"{len(report['new_symbols'])} added, {len(report['status_changes'])} status changes")


if __name__ == "__main__":
    main()
