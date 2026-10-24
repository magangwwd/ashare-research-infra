"""2018-02 Binance Spot 1m/5m Kline source crosscheck (read-only).

Download exactly two monthly ZIP archives plus official checksums; never fill
missing candles or write reconstructed data to the production V3 data source.
The report is audit evidence, not a repaired backtest.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
from audit_15m_gap import (
    UTC, MISSING_4H_OPEN, aggregate_15m, compare,
    download_verified, parse_archive
)

SECONDS = {"1m": 60, "5m": 300}
UTC_START = dt.datetime(2018, 2, 1, tzinfo=UTC)
UTC_END = dt.datetime(2018, 3, 1, tzinfo=UTC)


def aggregate_short(candles, start, interval):
    step_seconds = SECONDS[interval]
    expected = [start + dt.timedelta(seconds=i * step_seconds)
                for i in range(14400 // step_seconds)]
    missing = [t for t in expected if t not in candles]
    if missing:
        return None, missing
    bars = [candles[t] for t in expected]
    return (
        bars[0][0], max(x[1] for x in bars),
        min(x[2] for x in bars), bars[-1][3],
        sum(x[4] for x in bars),
    ), []


def gaps_in_source(candles, interval):
    step = dt.timedelta(seconds=SECONDS[interval])
    stamps = sorted(candles)
    gaps = []
    for before, after in zip(stamps, stamps[1:]):
        if after - before != step:
            gaps.append({
                "last_before_gap": before.isoformat(),
                "first_after_gap": after.isoformat(),
                "missing_interval_count": int((after-before) / step) - 1,
            })
    return gaps


def inspect_short(interval, candles, unaligned_rows, four):
    gap_windows = []
    for t in MISSING_4H_OPEN:
        aggregated, absent = aggregate_short(candles, t, interval)
        gap_windows.append({
            "utc_4h_open": t.isoformat(),
            "missing_short_bars": len(absent),
            "full_4h_rebuildable": not absent,
            "aggregate_ohlcv": aggregated,
        })
    missing_count = sum(x["missing_short_bars"] for x in gap_windows)
    mismatches, matched = [], 0
    for t, bar in sorted(four.items()):
        aggregate, absent = aggregate_short(candles, t, interval)
        if absent:
            continue
        matched += 1
        if not compare(bar, aggregate):
            mismatches.append(t.isoformat())
    discontinuities = gaps_in_source(candles, interval)
    opens = sorted(candles)
    return {
        "interval": interval,
        "aligned_monthly_bars": len(candles),
        "unaligned_bar_count": len(unaligned_rows),
        "unaligned_bar_examples": unaligned_rows[:10],
        "first_aligned_utc": opens[0].isoformat() if opens else None,
        "last_aligned_utc": opens[-1].isoformat() if opens else None,
        "discontinuity_count": len(discontinuities),
        "discontinuities_preview": discontinuities[:10],
        "missing_bars_in_original_seven_4h_windows": missing_count,
        "original_seven_4h_window_details": gap_windows,
        "existing_official_4h_bars_crosschecked": matched,
        "existing_4h_mismatches_count": len(mismatches),
        "existing_4h_mismatches_preview": mismatches[:10],
        "can_rebuild_all_seven_missing_4h": (
            missing_count == 0 and all(x["full_4h_rebuildable"] for x in gap_windows)
        ),
    }


def make_report(payloads=None):
    # Optional injection is for deterministic offline tests.
    blobs = {}
    digest = {}
    for interval in ("1m", "5m", "4h"):
        if payloads is None:
            blobs[interval], digest[interval] = download_verified(interval)
        else:
            blobs[interval], digest[interval] = payloads[interval]
    unusual_4h = []
    four = parse_archive(blobs["4h"], "4h", unusual_rows=unusual_4h)
    if unusual_4h:
        raise ValueError("Unaligned official 4h bars require separate investigation")
    evidence = {}
    for interval in ("1m", "5m"):
        unusual = []
        bars = parse_archive(blobs[interval], interval, unusual_rows=unusual)
        evidence[interval] = inspect_short(interval, bars, unusual, four)
    any_full = any(evidence[t]["can_rebuild_all_seven_missing_4h"] for t in ("1m", "5m"))
    safe_to_reconstruct = any(
        evidence[t]["can_rebuild_all_seven_missing_4h"]
        and evidence[t]["existing_4h_mismatches_count"] == 0
        and evidence[t]["existing_official_4h_bars_crosschecked"] >= 50
        for t in ("1m", "5m")
    )
    return {
        "checked_utc": dt.datetime.now(UTC).isoformat(),
        "dataset": "Binance BTCUSDT Spot 2018-02, official monthly klines",
        "checksums": digest,
        "all_three_official_sha256_verified": payloads is None,
        "original_4h_gap": "2018-02-08 04:00 to 2018-02-09 04:00 UTC",
        "read_only": True,
        "no_candles_fabricated": True,
        "pilot_status": (
            "POSSIBLE_RECONSTRUCTION_REQUIRES_SEPARATE_APPROVAL_AND_RETEST"
            if safe_to_reconstruct else
            "PARTIAL_SOURCE_EXISTS_BUT_4H_RECONSTRUCTION_NOT_VALIDATED"
            if any_full else
            "ORIGINAL_SEVEN_4H_GAP_NOT_RECONSTRUCTABLE_FROM_1M_OR_5M"
        ),
        "intervals": evidence,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        report = make_report()
    except Exception as e:
        report = {
            "pilot_status": "AUDIT_ERROR_NOT_VERIFIED",
            "error_class": type(e).__name__, "error": str(e)[:300],
            "checked_utc": dt.datetime.now(UTC).isoformat(),
        }
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        raise
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({
        "pilot_status": report["pilot_status"],
        "intervals": {
            i: {
                "missing_in_seven_windows": v["missing_bars_in_original_seven_4h_windows"],
                "unaligned": v["unaligned_bar_count"],
                "crosschecked": v["existing_official_4h_bars_crosschecked"],
                "discrepancies": v["existing_4h_mismatches_count"],
            } for i, v in report["intervals"].items()
        }
    }))


if __name__ == "__main__":
    main()
