"""Read-only Binance BTCUSDT 2018-02 15m gap-reconstruction audit.

This is a one-month pilot only. It does NOT change V3, replace official
4h archives, or fabricate price candles. UTC alignment is strict.
"""
import argparse
import csv
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import re
import urllib.request
import zipfile

ROOT = "https://data.binance.vision/data/spot/monthly/klines/BTCUSDT"
UTC = dt.timezone.utc
START = dt.datetime(2018, 2, 8, 0, 0, tzinfo=UTC)
END = dt.datetime(2018, 2, 9, 8, 0, tzinfo=UTC)
MISSING_4H_OPEN = [START + dt.timedelta(hours=4 * i) for i in range(1, 8)]


def download_verified(interval):
    name = f"BTCUSDT-{interval}-2018-02.zip"
    url = f"{ROOT}/{interval}/{name}"
    def read(u):
        with urllib.request.urlopen(
            urllib.request.Request(u, headers={"User-Agent": "BTC-gap-audit/1.0"}),
            timeout=50,
        ) as response:
            return response.read(20_000_000)
    blob = read(url)
    if not blob.startswith(b"PK"):
        raise ValueError(f"{name} is not ZIP")
    sha = hashlib.sha256(blob).hexdigest()
    official_checksum = read(url + ".CHECKSUM").decode("ascii", errors="replace").strip()
    if not re.search(rf"(?m)^{sha}\s+\*?{re.escape(name)}\s*$", official_checksum):
        raise ValueError(f"{name} SHA256 does not match official .CHECKSUM")
    return blob, sha


def parse_archive(blob, interval, unusual_rows=None):
    seconds = {"4h": 14400, "15m": 900}[interval]
    rows = {}
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        csv_files = [n for n in archive.namelist() if n.endswith(".csv")]
        if len(csv_files) != 1:
            raise ValueError(f"Expected 1 CSV, got {csv_files}")
        with archive.open(csv_files[0]) as raw:
            for i, r in enumerate(csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig")), 1):
                if not r or r[0].strip().lower() in ("open_time", "opentime"):
                    continue
                if len(r) < 6:
                    raise ValueError(f"short {interval} row {i}")
                stamp = int(r[0])
                opened = dt.datetime.fromtimestamp(stamp / (1_000_000 if stamp >= 10**15 else 1000), UTC)
                if int(opened.timestamp()) % seconds or opened.microsecond:
                    if unusual_rows is None:
                        raise ValueError(f"unaligned {interval} row {i}: {opened}")
                    unusual_rows.append({"csv_row": i, "utc_open": opened.isoformat(),
                                         "reason": "not aligned to official interval"})
                    continue
                v = tuple(map(float, r[1:6]))
                o, h, l, c, vol = v
                if not (0 < l <= o <= h and l <= c <= h and vol >= 0):
                    raise ValueError(f"invalid OHLCV {interval} row {i}")
                if opened in rows:
                    raise ValueError(f"duplicate {interval} at {opened}")
                rows[opened] = v
    return rows


def aggregate_15m(candles, start):
    times = [start + dt.timedelta(minutes=15 * i) for i in range(16)]
    present = [candles[t] for t in times if t in candles]
    if len(present) != 16:
        return None, [str(t) for t in times if t not in candles]
    return (present[0][0], max(v[1] for v in present),
            min(v[2] for v in present), present[-1][3],
            sum(v[4] for v in present)), []


def compare(old, new):
    tolerance_price = 1e-8
    tolerance_volume = 1e-7
    return all(abs(a-b) <= max(1e-7, abs(a)* (tolerance_volume if i == 4 else tolerance_price))
               for i, (a, b) in enumerate(zip(old, new)))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    payload_15, sha_15 = download_verified("15m")
    payload_4, sha_4 = download_verified("4h")
    unusual_15m = []
    fifteen = parse_archive(payload_15, "15m", unusual_rows=unusual_15m)
    four = parse_archive(payload_4, "4h")

    gaps = {}
    missing_15m = []
    for open_time in MISSING_4H_OPEN:
        reconstructed, absent = aggregate_15m(fifteen, open_time)
        gaps[open_time.isoformat()] = {
            "in_original_4h": open_time in four,
            "all_16_15m": not absent,
            "missing_15m_count": len(absent),
            "missing_15m": absent,
            "derived_ohlcv": reconstructed,
        }
        missing_15m += absent

    full_4h_intervals = [t for t in four if t >= dt.datetime(2018, 2, 1, tzinfo=UTC)]
    mismatches = []
    comparable = 0
    for t in sorted(full_4h_intervals):
        derived, absent = aggregate_15m(fifteen, t)
        if absent:
            continue
        comparable += 1
        if not compare(four[t], derived):
            mismatches.append(t.isoformat())

    fifteen_sorted = sorted(fifteen)
    discontinuities = [
        {"after": before.isoformat(), "before": after.isoformat(),
         "missing_15m": int((after-before).total_seconds()/900)-1}
        for before, after in zip(fifteen_sorted, fifteen_sorted[1:])
        if (after-before).total_seconds() != 900
    ]

    all_missing_rebuildable = all(
        not x["in_original_4h"] and x["all_16_15m"] for x in gaps.values()
    )
    status = (
        "PILOT_PASSES_RECONSTRUCTION_CHECKS_NOT_APPLIED_TO_BACKTEST"
        if all_missing_rebuildable and not mismatches and comparable >= 50
        else "PILOT_CANNOT_CERTIFY_FULL_REPAIR"
    )
    report = {
        "status": status,
        "symbol": "BTCUSDT", "venue": "Binance Spot",
        "purpose": "research only, read-only; original V3 4h data untouched",
        "month": "2018-02",
        "official_zip_sha256": {"15m": sha_15, "4h": sha_4},
        "official_checksums_verified": True,
        "unaligned_15m_rows_count": len(unusual_15m),
        "unaligned_15m_rows_preview": unusual_15m[:15],
        "source_15m_rows": len(fifteen), "source_4h_rows": len(four),
        "expected_missing_4h_opens": [t.isoformat() for t in MISSING_4H_OPEN],
        "target_missing_15m_total": len(missing_15m),
        "target_4h_reconstruction": gaps,
        "all_15m_discontinuities_count": len(discontinuities),
        "all_15m_discontinuities_preview": discontinuities[:12],
        "independent_overlapping_4h_windows_checked": comparable,
        "overlapping_4h_mismatch_count": len(mismatches),
        "overlapping_4h_mismatch_preview": mismatches[:15],
    }
    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in (
        "status", "source_15m_rows", "source_4h_rows", "unaligned_15m_rows_count", "target_missing_15m_total",
        "all_15m_discontinuities_count", "independent_overlapping_4h_windows_checked",
        "overlapping_4h_mismatch_count"
    )}, ensure_ascii=False))
    if status != "PILOT_PASSES_RECONSTRUCTION_CHECKS_NOT_APPLIED_TO_BACKTEST":
        raise SystemExit("Pilot found unrepairable missing data or disagreeing archives")


if __name__ == "__main__":
    main()
