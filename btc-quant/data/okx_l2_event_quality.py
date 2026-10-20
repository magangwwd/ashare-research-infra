"""Stream-audit actual official OKX L2 JSONL day without releasing individual ticks.

A valid/timely JSONL archive does NOT establish lossless exchange events.
"""
import argparse
import collections
import datetime as dt
import hashlib
import json
from pathlib import Path
import tarfile

EXPECTED_SHA256 = "829b1393dcf7b9072383092f620039ba15b8be0d7e0fd422e9b66a8afd8b5a7e"
DAY = dt.date(2026, 10, 10)
MAX_EVENTS = 15_000_000
MAX_LINE_BYTES = 16 * 1024 * 1024


def validate_price_levels(levels):
    if not isinstance(levels, list):
        raise ValueError("Orderbook side is not an array")
    if levels and (not isinstance(levels[0], list) or len(levels[0]) < 2):
        raise ValueError("Unexpected orderbook price level shape")
    for level in (levels[0:1] + levels[-1:] if levels else []):
        if not isinstance(level, list) or len(level) < 2:
            raise ValueError("Unexpected orderbook price level")
        if float(level[0]) <= 0 or float(level[1]) < 0:
            raise ValueError("Nonpositive price or negative size")


def audit_stream(lines, utc_date=DAY):
    start = int(dt.datetime.combine(utc_date, dt.time(), tzinfo=dt.timezone.utc).timestamp() * 1000)
    end = start + 86400_000
    actions = collections.Counter()
    minutes = set()
    total = 0
    minimum = maximum = previous = None
    stats = collections.Counter()
    max_gap_ms = 0
    for raw in lines:
        if not raw.strip():
            continue
        total += 1
        if total > MAX_EVENTS or len(raw) > MAX_LINE_BYTES:
            raise ValueError("Exceeded daily event/line cap")
        try:
            record = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"Invalid JSON at event {total}") from exc
        if not isinstance(record, dict) or not all(k in record for k in ("ts","instId","action","bids","asks")):
            raise ValueError(f"Missing official fields at event {total}")
        if record["instId"] != "BTC-USDT-SWAP":
            raise ValueError(f"Wrong instrument at event {total}")
        try:
            ts = int(record["ts"])
        except (ValueError, TypeError):
            raise ValueError(f"Invalid timestamp at event {total}")
        if not start <= ts < end:
            stats["events_outside_utc_day"] += 1
            if stats["events_outside_utc_day"] > 10:
                raise ValueError("Too many out-of-day L2 messages")
        try:
            validate_price_levels(record["bids"])
            validate_price_levels(record["asks"])
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Malformed L2 side at event {total}: {exc}") from exc
        actions[str(record["action"])] += 1
        if not record["bids"] and not record["asks"]:
            stats["both_sides_empty"] += 1
        if record["action"] in ("snapshot","partial") and record["bids"] and record["asks"]:
            if float(record["bids"][0][0]) >= float(record["asks"][0][0]):
                stats["crossed_snapshot_top"] += 1
        minimum = ts if minimum is None else min(minimum, ts)
        maximum = ts if maximum is None else max(maximum, ts)
        if previous is not None:
            gap = ts - previous
            if gap < 0: stats["out_of_order_timestamp_events"] += 1
            if gap == 0: stats["same_millisecond_consecutive"] += 1
            if gap > 5000: stats["gaps_over_5s"] += 1
            if gap > 60000: stats["gaps_over_60s"] += 1
            if gap > max_gap_ms: max_gap_ms = gap
        previous = ts
        if start <= ts < end:
            minutes.add((ts-start)//60000)
    if total == 0:
        raise ValueError("No parseable L2 events")
    return {
        "date_utc": utc_date.isoformat(), "instrument": "BTC-USDT-SWAP",
        "format": "OKX_L2_JSONL", "events_parsed": total,
        "first_event_utc": dt.datetime.fromtimestamp(minimum/1000, tz=dt.timezone.utc).isoformat(),
        "last_event_utc": dt.datetime.fromtimestamp(maximum/1000, tz=dt.timezone.utc).isoformat(),
        "event_actions": dict(sorted(actions.items())),
        "minutes_with_at_least_one_event": len(minutes),
        "minutes_in_utc_day": 1440,
        "max_positive_interevent_gap_seconds": max_gap_ms/1000,
        "counts": dict(sorted(stats.items())),
        "schema_validation": "PASS",
        "exchange_sequence_continuity": "NOT_VERIFIED_NO_SEQUENCE_IN_EXPOSED_JSON_FIELDS",
        "perfect_tick_reconstruction": "NOT_CLAIMED",
        "raw_market_events_in_report": False,
    }


def audit_file(path: Path, expected_sha=EXPECTED_SHA256):
    h=hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(8 * 1024 * 1024):
            h.update(block)
    if h.hexdigest() != expected_sha:
        raise ValueError("Official compressed file hash changed")
    with tarfile.open(path,"r:gz") as archive:
        members=archive.getmembers()
        if len(members)!=1 or not members[0].isfile():
            raise ValueError("Wrong official L2 tar structure")
        if Path(members[0].name).is_absolute() or ".." in Path(members[0].name).parts:
            raise ValueError("Unsafe tar member")
        with archive.extractfile(members[0]) as member:
            stats=audit_stream(member)
    stats["approved_compressed_sha256"]=expected_sha
    return stats


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--archive",required=True)
    p.add_argument("--out",required=True)
    a=p.parse_args()
    result=audit_file(Path(a.archive))
    path=Path(a.out)
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,indent=2,ensure_ascii=False)+"\n")
    print(json.dumps(result,ensure_ascii=False))


if __name__=="__main__":
    main()
