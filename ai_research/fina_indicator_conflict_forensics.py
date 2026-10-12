"""Read-only live forensics for four ambiguous Tushare fina_indicator source groups.

No signals, no strategy changes, no Clean promotion. Outputs source-row diffs and
the intersection with the immutable Clean column schema, with provenance.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from datetime import datetime, timezone, date
from pathlib import Path

CASES = (
    ("002107.SZ", "20260331"),
    ("600641.SH", "20260331"),
    ("002568.SZ", "20260630"),
    ("002296.SZ", "20260630"),
)
METADATA = ("ann_date", "f_ann_date", "update_flag", "report_type", "comp_type")
IDENTITY = ("ts_code", "end_date")
AUTHORIZATION = "PAPER_ONLY_NO_ENTRY_AUTHORIZATION"


def scalar(v):
    import pandas as pd
    if v is None or v is pd.NA:
        return None
    try:
        missing = pd.isna(v)
        if isinstance(missing, bool) and missing:
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(v, (float, int)):
        return None if isinstance(v, float) and not math.isfinite(v) else v
    if hasattr(v, "item"):
        return scalar(v.item())
    return str(v)


def fingerprint(record):
    blob = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def analyze_frame(frame, symbol, period, captured_utc):
    """Conservatively audit only the latest same-metadata group from an issuer-period."""
    import pandas as pd
    from data_pipeline.clean import SCHEMAS, schema_version
    entry = {
        "symbol": symbol, "period": period, "dataset": "fina_indicator",
        "provider": "TUSHARE_PRO", "captured_utc": captured_utc,
        "status": "UNRESOLVED", "raw_rows": 0, "same_key_rows": 0,
        "conflicts_all_source_fields": {}, "conflicts_in_clean_projection": {},
        "source_fingerprints": [], "versions": [],
        "clean_schema_version": schema_version("fina_indicator"),
        "release_to_clean": False, "official_issuer_document_crosscheck": "PENDING",
    }
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        entry["status"] = "NO_SOURCE_ROWS"
        return entry
    for col in ("ts_code", "end_date", "ann_date"):
        if col not in frame:
            entry["status"] = "MISSING_COLUMN:" + col
            return entry
    rows = frame.loc[frame.ts_code.astype(str).eq(symbol) &
                     frame.end_date.astype(str).eq(period)].copy()
    rows = rows.loc[rows.ann_date.astype(str).str.fullmatch(r"\d{8}") &
                    rows.ann_date.astype(str).le(date.fromisoformat(captured_utc[:10]).strftime("%Y%m%d"))]
    entry["raw_rows"] = len(rows)
    if rows.empty:
        entry["status"] = "NO_DATED_MATCHING_ROWS"
        return entry
    key_cols = [c for c in METADATA if c in rows]
    rows = rows.sort_values(key_cols, kind="stable", na_position="first")
    latest = rows.iloc[-1]
    tied = rows.copy()
    for col in key_cols:
        value = latest[col]
        match = tied[col].isna() if pd.isna(value) else tied[col].astype(str).eq(str(value))
        tied = tied.loc[match]
    entry["same_key_rows"] = len(tied)
    entry["revision_key"] = {k: scalar(latest[k]) for k in key_cols}
    if len(tied) < 2:
        entry["status"] = "LATEST_REVISION_SINGLE_ROW"
        return entry
    saved_cols = {f.source for f in SCHEMAS["fina_indicator"] if f.source != "security_id"}
    for index, (_, r) in enumerate(tied.iterrows()):
        item = {c: scalar(r[c]) for c in sorted(tied.columns) if not c.startswith("_")}
        entry["source_fingerprints"].append(fingerprint(item))
        entry["versions"].append({"source_variant": index+1,
                                  "clean_required_metrics": {c: item.get(c) for c in ("roe","debt_to_assets","current_ratio","eps","dt_eps","extra_item","profit_dedt")},
                                  "source_sha256": fingerprint(item)})
    for col in sorted(c for c in tied.columns if not c.startswith("_")):
        vals = [scalar(value) for value in tied[col]]
        # Distinguish NULL from zero. Exact equality for original source evidence.
        unique = {json.dumps(v, ensure_ascii=False, sort_keys=True, default=str) for v in vals}
        if len(unique) > 1:
            entry["conflicts_all_source_fields"][col] = vals
            if col in saved_cols:
                entry["conflicts_in_clean_projection"][col] = vals
    entry["status"] = ("MATERIAL_CLEAN_FIELD_CONFLICT"
                       if entry["conflicts_in_clean_projection"]
                       else "OUT_OF_SCHEMA_CONFLICT_ONLY")
    entry["release_to_clean"] = False  # An audit never approves or changes the collector.
    return entry


def run(out, client):
    now = datetime.now(timezone.utc).isoformat()
    audit = {"version": "FIVE_FINA_CONFLICT_FORENSICS_V1",
             "status": "FORENSIC_ONLY_NOT_CLEAN", "captured_utc": now,
             "source": "TUSHARE_PRO_LIVE_QUERY", "authorization": AUTHORIZATION,
             "four_cases": [], "issues": [], "clean_promotion_authorized": False}
    for sym, period in CASES:
        try:
            frame = client.query("fina_indicator", ts_code=sym, period=period)
            audit["four_cases"].append(analyze_frame(frame, sym, period, now))
        except Exception as exc:
            token = os.environ.get("TUSHARE_TOKEN", "")
            reason = str(exc).replace(token, "[REDACTED]") if token else str(exc)
            audit["four_cases"].append({"symbol": sym, "period": period,
                                        "status": "FETCH_FAILED", "reason": reason[:400]})
            audit["issues"].append(sym + ":FETCH_FAILED")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
    summary = {f'{r["symbol"]}:{r["period"]}': {
        "status": r["status"], "raw_rows": r.get("raw_rows"),
        "conflicting_clean_fields": list(r.get("conflicts_in_clean_projection", {}))}
               for r in audit["four_cases"]}
    print(json.dumps({"status": audit["status"], "cases": summary}, ensure_ascii=False))
    return 0 if len(audit["four_cases"]) == 4 and not audit["issues"] else 2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        raise SystemExit("TUSHARE_TOKEN environment secret not configured")
    import tushare as ts
    ts.set_token(token)
    return run(args.output, ts.pro_api(token))


if __name__ == "__main__":
    raise SystemExit(main())
