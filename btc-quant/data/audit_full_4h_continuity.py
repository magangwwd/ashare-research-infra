"""Audit every 4h open in Binance Vision BTCUSDT Spot monthly ZIPs.

No fabricated or forward-filled candles; identify exact gaps, complete
UTC trading days and candidate continuous time segments only. A "continuous"
segment has valid OHLCV and timestamps, not proven execution realism.
"""
import argparse
import calendar
import csv
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

UTC = dt.timezone.utc
INTERVAL = dt.timedelta(hours=4)
MONTH_PATTERN = re.compile(r"^BTCUSDT-4h-(\d{4})-(\d{2})\.zip$")


def expected_month(year, month):
    ndays = calendar.monthrange(year, month)[1]
    start = dt.datetime(year, month, 1, tzinfo=UTC)
    return [start + i*INTERVAL for i in range(ndays*6)]


def parse_month(filename, blob):
    match = MONTH_PATTERN.fullmatch(filename)
    if not match:
        raise ValueError(f"Unexpected filename: {filename}")
    year, month = int(match[1]), int(match[2])
    allowed = set(expected_month(year, month))
    result, anomalies = {}, []
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        names = [n for n in archive.namelist() if n.lower().endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"Archive must contain exactly one CSV: {filename}")
        with archive.open(names[0]) as raw:
            for line_number, row in enumerate(csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig")), 1):
                if not row or row[0].lower().strip() in ("open_time", "opentime"):
                    continue
                if len(row) < 6:
                    anomalies.append({"row": line_number, "reason": "short_row"})
                    continue
                try:
                    n = int(row[0])
                    seconds = n / (1_000_000 if n >= 10**15 else 1_000 if n >= 10**12 else 1)
                    opened = dt.datetime.fromtimestamp(seconds, UTC)
                    op, high, low, close, volume = map(float, row[1:6])
                    valid = 0 < low <= min(op, close) <= max(op, close) <= high and volume >= 0
                except (OverflowError, ValueError, OSError) as exc:
                    anomalies.append({"row": line_number, "reason": "invalid_numeric", "detail": str(exc)[:100]})
                    continue
                if opened not in allowed:
                    anomalies.append({"row": line_number, "reason": "unexpected_4h_open",
                                      "time": opened.isoformat()})
                    continue
                if not valid:
                    anomalies.append({"row": line_number, "reason": "bad_ohlcv",
                                      "time": opened.isoformat()})
                    continue
                if opened in result:
                    anomalies.append({"row": line_number, "reason": "duplicate_open",
                                      "time": opened.isoformat()})
                    continue
                result[opened] = (op, high, low, close, volume)
    return result, anomalies


def contiguous_daily_ranges(full_days):
    if not full_days:
        return []
    sorted_days = sorted(full_days)
    ranges = []
    start = end = sorted_days[0]
    for d in sorted_days[1:]:
        if d - end == dt.timedelta(days=1):
            end = d
        else:
            ranges.append({"utc_start": start.isoformat(), "utc_end": end.isoformat(),
                           "days": (end-start).days+1})
            start = end = d
    ranges.append({"utc_start": start.isoformat(), "utc_end": end.isoformat(),
                   "days": (end-start).days+1})
    return ranges


def audit_months(files):
    listed = {}
    seen = set()
    for filename, blob in files:
        match = MONTH_PATTERN.fullmatch(filename)
        if not match:
            raise ValueError(f"Unexpected archive: {filename}")
        key = (int(match[1]), int(match[2]))
        if key in seen:
            raise ValueError(f"Duplicate archive month: {key}")
        seen.add(key)
        bars, anomalies = parse_month(filename, blob)
        listed[key] = {
            "file": filename,
            "file_sha256": hashlib.sha256(blob).hexdigest(),
            "accepted_4h": bars,
            "anomalies": anomalies,
        }
    if not listed:
        raise ValueError("No monthly sources provided")

    lo, hi = min(listed), max(listed)
    months = [(y,m) for y in range(lo[0],hi[0]+1)
              for m in range(1,13)
              if lo <= (y,m) <= hi]
    month_records, full_days = [], set()
    total_missing = total_bad = total_bars = 0
    for y,m in months:
        key = (y,m)
        expected = expected_month(y,m)
        record = listed.get(key)
        accepted = record["accepted_4h"] if record else {}
        missing = [d for d in expected if d not in accepted]
        anomalies = record["anomalies"] if record else []
        full_count = 0
        for day in range(1, calendar.monthrange(y,m)[1]+1):
            date = dt.date(y,m,day)
            start = dt.datetime(y,m,day,tzinfo=UTC)
            if all(start+i*INTERVAL in accepted for i in range(6)):
                full_days.add(date)
                full_count += 1
        total_missing += len(missing)
        total_bad += len(anomalies)
        total_bars += len(accepted)
        month_records.append({
            "month": f"{y:04d}-{m:02d}",
            "source": record["file"] if record else None,
            "sha256": record["file_sha256"] if record else None,
            "expected_4h": len(expected),
            "verified_4h": len(accepted),
            "missing_4h_count": len(missing),
            "missing_4h_preview": [t.isoformat() for t in missing[:18]],
            "anomaly_count": len(anomalies),
            "anomaly_preview": anomalies[:12],
            "complete_utc_days": full_count,
        })
    spans = contiguous_daily_ranges(full_days)
    longest = max(spans, key=lambda s:s["days"]) if spans else None
    missing_months = [x["month"] for x in month_records if x["missing_4h_count"]]
    return {
        "status": "HAS_MISSING_4H_REQUIRES_SEGMENTED_RESEARCH"
            if total_missing or total_bad else "TIME_CONTINUITY_OK_REQUIRES_OTHER_VALIDATION",
        "source": "Binance Vision BTCUSDT Spot monthly 4h ZIP",
        "month_start": f"{lo[0]:04d}-{lo[1]:02d}",
        "month_end": f"{hi[0]:04d}-{hi[1]:02d}",
        "months_checked": len(months),
        "missing_archive_months": [r["month"] for r in month_records if r["source"] is None],
        "verified_4h_total": total_bars,
        "missing_4h_total": total_missing,
        "anomaly_total": total_bad,
        "missing_months": missing_months,
        "full_utc_days": len(full_days),
        "contiguous_utc_day_spans": spans,
        "longest_complete_utc_day_span": longest,
        "audit_interpretation": "No gap fill, interpolation, cross-exchange splice or implied 2018-2026 full-history V3 result.",
        "month_evidence": month_records,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()
    files = [(x.name, x.read_bytes()) for x in sorted(Path(args.source).glob("BTCUSDT-4h-*.zip"))]
    result = audit_months(files)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        k: result[k] for k in ("status","months_checked","verified_4h_total",
                              "missing_4h_total","anomaly_total",
                              "missing_months","full_utc_days",
                              "longest_complete_utc_day_span")
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
