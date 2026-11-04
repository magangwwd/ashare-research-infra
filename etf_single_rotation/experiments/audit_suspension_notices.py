"""Cross-reference actual missing ETF sessions to publicly documented trading halts.

This stage NEVER repairs prices, writes fabricated bars or changes adjusted
ETF returns. Published suspension documents can explain absent trading quotes,
but ETF unit conversions and reinvested distributions still need price audit.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
NOTICE_PATH = ROOT / "metadata" / "announced_suspensions.json"
GAPS = ROOT / "results" / "data_quality_gaps.csv"
DATA = ROOT / "data" / "prices.csv"
OUT = ROOT / "results"
EXPECTED_SET = {
    "510300.SH", "510500.SH", "159915.SZ", "510050.SH",
    "510880.SH", "518880.SH", "512100.SH", "512690.SH"
}


def validate_registry(registry):
    if registry.get("schema_version") != 1 or not registry.get("evidence"):
        raise ValueError("Missing or unexpected halt notice registry")
    events = {}
    for entry in registry["evidence"]:
        key = (str(entry.get("symbol", "")), str(entry.get("date", "")))
        if key[0] not in EXPECTED_SET or pd.isna(pd.to_datetime(key[1], errors="coerce")):
            raise ValueError("Invalid ETF or suspension date")
        if key in events:
            raise ValueError("Duplicate ETF/date suspension notice")
        url = str(entry.get("source_url", ""))
        parts = urlparse(url)
        if parts.scheme != "https" or not parts.netloc:
            raise ValueError("Announcement must have actual HTTPS evidence URL")
        if not entry.get("source_type") or not entry.get("announcement_reason"):
            raise ValueError("Announcement requires source classification and reason")
        events[key] = entry
    return events


def crosscheck(registry, gaps: pd.DataFrame, prices: pd.DataFrame):
    known = validate_registry(registry)
    required = {"symbol", "date", "issue"}
    if not required.issubset(gaps.columns):
        raise ValueError("Data quality gaps lack symbol/date/issue")
    if not {"symbol", "date"}.issubset(prices.columns):
        raise ValueError("Missing real price keys")
    current_quotes = set(zip(prices.symbol.astype(str), prices.date.astype(str)))
    if current_quotes.intersection(known):
        raise ValueError("Real quote exists on declared complete halt day; manual audit necessary")
    observed = gaps.loc[
        gaps.issue == "MISSING_PRICE_ON_EXCHANGE_OPEN_DAY", ["symbol", "date"]
    ].drop_duplicates()
    observed_keys = set(zip(observed.symbol.astype(str), observed.date.astype(str)))
    extraneous = sorted(set(known) - observed_keys)
    unexplained = sorted(observed_keys - set(known))
    records = []
    for sym, d in sorted(observed_keys):
        notice = known.get((sym, d))
        records.append({
            "symbol": sym, "date": d,
            "classification": "PUBLISHED_TRADING_HALT" if notice else "UNEXPLAINED_MISSING_SESSION",
            "source_type": notice.get("source_type") if notice else "",
            "announcement_reason": notice.get("announcement_reason") if notice else "",
            "source_url": notice.get("source_url") if notice else "",
            "supporting_url": notice.get("supporting_url", "") if notice else "",
            "no_synthetic_price": True,
        })
    status = ("ALL_GAPS_HAVE_ANNOUNCED_SUSPENSION_EVIDENCE"
              if not unexplained and not extraneous
              else "REVIEW_REQUIRED_GAP_SOURCE_CONFLICT")
    details = pd.DataFrame(records, columns=[
        "symbol", "date", "classification", "source_type",
        "announcement_reason", "source_url", "supporting_url",
        "no_synthetic_price"
    ])
    summary = {
        "status": status,
        "observed_provider_missing_exchange_session_rows": len(observed_keys),
        "documented_announced_halt_rows": len(observed_keys & set(known)),
        "unexplained_rows": len(unexplained),
        "unexplained_keys": [{"symbol": a, "date": b} for a, b in unexplained],
        "notice_without_missing_quote_keys": [
            {"symbol": a, "date": b} for a, b in extraneous
        ],
        "registry_sha256": hashlib.sha256(
            json.dumps(registry, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest(),
        "does_not_insert_synthetic_trading_quotes": True,
        "price_corporate_action_and_distribution_validation": "UNVERIFIED",
        "readiness": "NOT_FOR_TRADING",
        "scope_note": "Public suspension announcements explain missing exchange-session quotes; they do not independently certify fund_adj split and cash dividend total-return effects.",
    }
    return details, summary


def main():
    for p in (NOTICE_PATH, GAPS, DATA):
        if not p.is_file():
            raise SystemExit(f"Required independent suspension evidence input missing: {p}")
    registry = json.loads(NOTICE_PATH.read_text("utf-8"))
    gaps = pd.read_csv(GAPS, dtype=str)
    prices = pd.read_csv(DATA, usecols=["symbol", "date"], dtype=str)
    details, summary = crosscheck(registry, gaps, prices)
    OUT.mkdir(parents=True, exist_ok=True)
    details.to_csv(OUT / "documented_suspension_gaps.csv", index=False)
    (OUT / "documented_suspension_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
    print("ETF_SUSPENSION_CROSSCHECK " + json.dumps(summary, ensure_ascii=False))
    if summary["status"] != "ALL_GAPS_HAVE_ANNOUNCED_SUSPENSION_EVIDENCE":
        raise SystemExit("Unexplained missing session or unsupported notice remains")


if __name__ == "__main__":
    main()
