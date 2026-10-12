"""Separate PIT-gated, human-verified public filing sidecar (not Clean ETL)."""
import argparse
import json
import math
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

AUTHORIZATION = "PAPER_ONLY_NO_ENTRY_AUTHORIZATION"
SYMBOLS = {"600351.SH", "002107.SZ", "600641.SH", "002568.SZ", "002296.SZ"}
REQUIRED = ("revenue_cny", "attributable_net_profit_cny", "deducted_net_profit_cny",
            "operating_cashflow_cny", "total_assets_cny", "attributable_equity_cny")
OPTIONAL = ("revenue_yoy_pct", "attributable_net_profit_yoy_pct",
            "deducted_profit_yoy_pct", "operating_cashflow_yoy_pct")

def load_records(path):
    records = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(records, list):
        raise ValueError("Disclosure seed must be a JSON array")
    seen = set()
    for r in records:
        if not isinstance(r, dict) or not {"symbol", "name", "report_period", "published_at",
                                            "captured_at", "source_url", "source_kind", "financials"} <= r.keys():
            raise ValueError("Disclosure record missing provenance fields")
        if r["symbol"] in seen:
            raise ValueError("Duplicate disclosure symbol")
        seen.add(r["symbol"])
        if r["symbol"] not in SYMBOLS:
            raise ValueError("Disclosure for unexpected symbol")
        report, published, captured = (date.fromisoformat(r[k])
                                        for k in ("report_period", "published_at", "captured_at"))
        if report > published or published > captured:
            raise ValueError("Invalid disclosure reporting/publication/capture chronology")
        u = urlparse(r["source_url"])
        if u.scheme != "https" or not u.netloc or r["source_kind"] != "full_company_report_mirrored_on_sina":
            raise ValueError("Invalid source provenance")
        facts = r["financials"]
        if not isinstance(facts, dict) or not set(REQUIRED) <= facts.keys():
            raise ValueError("Missing financial headline fields")
        for k in REQUIRED + OPTIONAL:
            v = facts.get(k)
            if k in REQUIRED and v is None:
                raise ValueError("Missing financial headline: " + k)
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))
                                  or not math.isfinite(v)):
                raise ValueError("Nonfinite/invalid figure: " + k)
    return records

def evaluate(path, as_of, target_symbols):
    """Conservative close-only rule: date-only capture is eligible from NEXT day."""
    day = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of
    records = load_records(path)
    accepted = {}
    excluded = []
    for r in records:
        if r["symbol"] not in target_symbols:
            continue
        p, c = date.fromisoformat(r["published_at"]), date.fromisoformat(r["captured_at"])
        if p >= day or c >= day:
            excluded.append({"symbol": r["symbol"], "reason": "NOT_CAPTURED_BEFORE_AS_OF_CLOSE"})
            continue
        accepted[r["symbol"]] = r
    coverage = {s: s in accepted for s in sorted(target_symbols)}
    return accepted, {
        "source": "MANUAL_VERIFIED_COMPANY_REPORT_MIRROR_NOT_CLEAN",
        "as_of": day.isoformat(), "authorization": AUTHORIZATION,
        "target_coverage": coverage, "covered": sum(coverage.values()),
        "total": len(coverage), "excluded": excluded,
        "does_not_establish_clean_etl_coverage": True
    }

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--path", type=Path, required=True)
    p.add_argument("--validate-only", action="store_true")
    args = p.parse_args()
    records = load_records(args.path)
    if args.validate_only:
        if {r["symbol"] for r in records} != SYMBOLS:
            raise ValueError("Expected exactly the five named symbols")
        print(json.dumps({"status": "VALIDATED_SOURCE_ROWS", "count": len(records),
                          "mode": "MANUAL_VERIFIED_NOT_CLEAN", "authorization": AUTHORIZATION}))
    else:
        p.error("--validate-only required")

if __name__ == "__main__":
    main()
