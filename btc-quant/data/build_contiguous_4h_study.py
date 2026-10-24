"""Build STRICTLY CONTIGUOUS 4h->1d research input, never bridge missing bars.

Preselected historical-data availability is discovered retrospectively; this
study is NOT independent out-of-sample validation and does not repair V3
original 2018-2026 historical archive or change frozen forward logic.
"""
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from audit_full_4h_continuity import audit_months, parse_month
from validate import aggregate_daily, save_daily

UTC = dt.timezone.utc


def build_segment(paths, dest, min_days=2000):
    files = [(p.name, p.read_bytes()) for p in sorted(paths)]
    coverage = audit_months(files)
    chosen = coverage["longest_complete_utc_day_span"]
    if not chosen or chosen["days"] < min_days:
        raise ValueError(f"No complete contiguous 4h span meets {min_days} full UTC days")
    first = dt.date.fromisoformat(chosen["utc_start"])
    last = dt.date.fromisoformat(chosen["utc_end"])
    if (last-first).days + 1 != chosen["days"]:
        raise ValueError("Coverage ledger days mismatch")
    accepted = {}
    source_hash = hashlib.sha256()
    for filename, payload in files:
        source_hash.update(filename.encode() + b"\0" + payload)
        month_bars, anomalies = parse_month(filename, payload)
        # Extra records are never silently dropped from the selected study.
        if any(
            first <= dt.date.fromisoformat(x["time"][:10]) <= last
            for x in anomalies if "time" in x
        ):
            raise ValueError("Source bar anomaly inside selected continuous span")
        for t, v in month_bars.items():
            if first <= t.date() <= last:
                if t in accepted:
                    raise ValueError(f"duplicate 4h bar between archives: {t.isoformat()}")
                accepted[t] = v
    if len(accepted) != chosen["days"] * 6:
        raise ValueError("Refusing mismatched 4h count for continuous study")
    days, dropped = aggregate_daily(accepted)
    if dropped or len(days) != chosen["days"]:
        raise ValueError("Refusing partial or dropped UTC days")
    if days[0]["date"] != first.isoformat() or days[-1]["date"] != last.isoformat():
        raise ValueError("Actual extracted UTC endpoints differ from approved span")
    out = Path(dest)
    out.mkdir(parents=True, exist_ok=True)
    old_report = save_daily(days, out, source_hash.hexdigest(), len(accepted), [])
    boundary = {
        "dataset": "BINANCE_SPOT_4H_COMPLETE_CONTIGUOUS_SPAN_RESEARCH_ONLY",
        "start_utc": first.isoformat(), "end_utc": last.isoformat(),
        "days": len(days), "4h_candles": len(accepted),
        "source_months_considered": coverage["months_checked"],
        "global_months_missing_4h": coverage["missing_months"],
        "global_4h_bars_missing": coverage["missing_4h_total"],
        "global_4h_bar_anomalies": coverage["anomaly_total"],
        "chosen_from_retrospective_data_coverage": True,
        "not_full_2018_2026_backtest": True,
        "not_issue_26_repaired": True,
        "not_real_forward": True,
        "no_missing_bars_filled": True,
        "no_multiple_segments_spliced": True,
        "research_only": True, "live_trading": False,
        "source_zip_sha256_combined": source_hash.hexdigest(),
        "validation_report": old_report,
    }
    (out / "source_boundary.json").write_text(
        json.dumps(boundary, ensure_ascii=False, indent=2) + "\n"
    )
    return boundary


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--source", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--min-days", type=int, default=2000)
    args = p.parse_args()
    paths = sorted(Path(args.source).glob("BTCUSDT-4h-*.zip"))
    boundary = build_segment(paths, args.out, args.min_days)
    print(json.dumps({
        k: boundary[k] for k in ("start_utc", "end_utc", "days", "4h_candles",
            "global_4h_bars_missing", "not_issue_26_repaired")
    }))


if __name__ == "__main__":
    main()
