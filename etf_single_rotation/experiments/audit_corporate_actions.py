"""Read-only ETF corporate action/fund-adjustment diagnostic; NOT_FOR_TRADING.

This is NOT independent certification of total-return prices. Never overwrite
provider prices or use synthetic observations as historical quotations.
"""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "metadata" / "corporate_action_evidence.json"
PRICES = ROOT / "data" / "prices.csv"
OUT = ROOT / "results"
ETF_SET = {
    "510300.SH", "510500.SH", "159915.SZ", "510050.SH",
    "510880.SH", "518880.SH", "512100.SH", "512690.SH",
}
TYPES = {"UNIT_SPLIT", "UNIT_CONSOLIDATION", "CASH_DIVIDEND"}


def _positive(value, label):
    # bool is an int subclass: True would otherwise fabricate a 1-CNY dividend
    # or a genuine-looking price/factor observation in a research diagnostic.
    if isinstance(value, bool):
        raise ValueError(f"Invalid {label}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {label}") from exc
    if not (0 < number < float("inf")):
        raise ValueError(f"Invalid {label}")
    return number


def validate_events(registry):
    if registry.get("schema_version") != 1 or not registry.get("events"):
        raise ValueError("Missing corporate-action evidence")
    keys = set()
    for event in registry["events"]:
        symbol, event_date, kind = (
            event.get("symbol"), event.get("event_date"), event.get("type")
        )
        if symbol not in ETF_SET or kind not in TYPES:
            raise ValueError("Unexpected ETF or corporate-action type")
        try:
            if date.fromisoformat(event_date).isoformat() != event_date:
                raise ValueError("Noncanonical event date")
        except (TypeError, ValueError) as exc:
            raise ValueError("Invalid corporate-action date") from exc
        key = (symbol, event_date, kind)
        if key in keys:
            raise ValueError("Duplicate corporate-action event")
        keys.add(key)
        url = urlparse(str(event.get("source_url", "")))
        if url.scheme != "https" or not url.netloc:
            raise ValueError("Corporate action lacks HTTPS source")
        if kind == "CASH_DIVIDEND":
            _positive(event.get("dividend_per_current_share_cny"), "dividend")
            # Python accepts compact YYYYMMDD as ISO, but downstream record-
            # date keys and cash-availability evidence require YYYY-MM-DD.
            # Reject missing, non-string and noncanonical dates at ingestion.
            try:
                registration_raw = event["registration_date"]
                payment_raw = event["cash_payment_date"]
                if not isinstance(registration_raw, str) or not isinstance(payment_raw, str):
                    raise ValueError("Date fields must be strings")
                registration = date.fromisoformat(registration_raw)
                payment = date.fromisoformat(payment_raw)
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("Invalid cash dividend registration/payment date") from exc
            if (registration.isoformat() != registration_raw
                    or payment.isoformat() != payment_raw):
                raise ValueError("Noncanonical cash dividend registration/payment date")
            if not (registration <= date.fromisoformat(event_date) <= payment):
                raise ValueError("Cash dividend registration/payment chronology invalid")
        else:
            ratio = _positive(event.get("new_units_per_old_unit"), "unit ratio")
            if (kind == "UNIT_SPLIT" and ratio <= 1) or (
                kind == "UNIT_CONSOLIDATION" and ratio >= 1
            ):
                raise ValueError("Unit ratio contradicts event type")
    return registry["events"]


def analyze(registry, prices):
    """Calculate diagnostic differences only; never silently repair prices."""
    events = validate_events(registry)
    required = {"date", "symbol", "raw_close", "adj_factor"}
    by_symbol, seen = {}, set()
    for row in prices:
        if not required.issubset(row):
            raise ValueError("Price row missing raw_close or adj_factor")
        symbol, trading_day = row["symbol"], row["date"]
        if symbol not in ETF_SET:
            raise ValueError("Unexpected price symbol")
        # Python accepts compact YYYYMMDD, but the research ledger and
        # evidence manifests require canonical YYYY-MM-DD market dates.
        # Reject malformed/non-string dates instead of silently normalizing.
        if not isinstance(trading_day, str):
            raise ValueError("Invalid real price date")
        try:
            day = date.fromisoformat(trading_day)
        except ValueError as exc:
            raise ValueError("Invalid real price date") from exc
        if day.isoformat() != trading_day:
            raise ValueError("Noncanonical real price date")
        key = (symbol, day)
        if key in seen:
            raise ValueError("Duplicate real price quote")
        seen.add(key)
        by_symbol.setdefault(symbol, []).append((
            day, _positive(row["raw_close"], "raw close"),
            _positive(row["adj_factor"], "adjustment factor")
        ))
    for rows in by_symbol.values():
        rows.sort(key=lambda x: x[0])

    diagnostics = []
    for event in events:
        symbol = event["symbol"]
        when = date.fromisoformat(event["event_date"])
        kind = event["type"]
        rows = by_symbol.get(symbol, [])
        before = next((row for row in reversed(rows) if row[0] < when), None)
        after = next((row for row in rows if row[0] >= when), None)
        if before is None or after is None:
            raise ValueError(f"No genuine quotes bracketing {symbol} {when}")
        d0, p0, a0 = before
        d1, p1, a1 = after
        ratio = (
            float(event["new_units_per_old_unit"])
            if kind != "CASH_DIVIDEND" else 1.0
        )
        dividend = (
            float(event["dividend_per_current_share_cny"])
            if kind == "CASH_DIVIDEND" else 0.0
        )
        economic_return = (ratio * p1 + dividend) / p0 - 1.0
        adjusted_return = (p1 * a1) / (p0 * a0) - 1.0
        difference_bp = 10000.0 * (adjusted_return - economic_return)
        # An ex-date without a genuine same-day quote cannot be treated as
        # an ordinary dividend-price comparison, even if the next quote is
        # only one session later and the factor arithmetic happens to match.
        ex_date_quote_missing = kind == "CASH_DIVIDEND" and d1 != when
        diagnostics.append({
            "symbol": symbol, "event_date": str(when), "type": kind,
            "quote_before": str(d0), "quote_after": str(d1),
            "quote_span_calendar_days": (d1 - d0).days,
            "raw_close_before": p0, "raw_close_after": p1,
            "adj_factor_before": a0, "adj_factor_after": a1,
            "new_units_per_old_unit": ratio,
            "dividend_per_old_unit_cny": dividend,
            "cash_payment_date": event.get("cash_payment_date", ""),
            "economic_event_window_return": economic_return,
            "fund_adj_event_window_return": adjusted_return,
            "fund_adj_minus_event_return_bp": difference_bp,
            "ex_date_quote_missing": ex_date_quote_missing,
            "needs_manual_review": (
                abs(difference_bp) > 10 or (d1 - d0).days > 14
                or ex_date_quote_missing
            ),
            "source_url": event["source_url"],
            "status": ("MISSING_EX_DATE_QUOTE_REVIEW_REQUIRED" if ex_date_quote_missing
                       else "DIAGNOSTIC_ONLY_NOT_TOTAL_RETURN_CERTIFICATION"),
        })
    return diagnostics



def candidate_dividend_coverage(registry, candidates, *, start_date="2019-01-02"):
    """Expose missing *candidate* cash events, without treating lists as primary proof.

    This comparison cannot certify issuer completeness, account settlement,
    adjusted prices, or total returns. It never mutates the event registry.
    """
    from decimal import Decimal, InvalidOperation

    events = validate_events(registry)
    if candidates.get("schema_version") != 1 or candidates.get("symbol") not in ETF_SET:
        raise ValueError("Invalid dividend candidate metadata")
    symbol = candidates["symbol"]
    cutoff = date.fromisoformat(start_date)
    if cutoff.isoformat() != start_date:
        raise ValueError("Noncanonical candidate cutoff")
    raw = candidates.get("events")
    if not isinstance(raw, list) or not raw:
        raise ValueError("Missing dividend candidate rows")
    known = {
        (event["symbol"], event["event_date"]): event
        for event in events if event["type"] == "CASH_DIVIDEND"
    }
    seen, missing, mismatched, checked = set(), [], [], 0
    candidate_dates_in_window = set()
    for row in raw:
        if not isinstance(row, dict):
            raise ValueError("Invalid dividend candidate row")
        fields = ("registration_date", "ex_dividend_date",
                  "announced_payment_date")
        for field in fields:
            value = row.get(field)
            if not isinstance(value, str):
                raise ValueError("Invalid candidate date")
            try:
                parsed = date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("Invalid candidate date") from exc
            if parsed.isoformat() != value:
                raise ValueError("Noncanonical candidate date")
        ex_date = row["ex_dividend_date"]
        if ex_date in seen:
            raise ValueError("Duplicate dividend candidate")
        seen.add(ex_date)
        if not (row["registration_date"] <= ex_date <= row["announced_payment_date"]):
            raise ValueError("Invalid candidate dividend chronology")
        try:
            amount = Decimal(str(row["dividend_per_share_cny"]))
        except (KeyError, InvalidOperation, ValueError, TypeError) as exc:
            raise ValueError("Invalid candidate dividend amount") from exc
        if not amount.is_finite() or amount <= 0:
            raise ValueError("Invalid candidate dividend amount")
        if ex_date < start_date:
            continue
        checked += 1
        candidate_dates_in_window.add(ex_date)
        registered = known.get((symbol, ex_date))
        if registered is None:
            missing.append(ex_date)
        elif (registered["registration_date"] != row["registration_date"]
              or registered["cash_payment_date"] != row["announced_payment_date"]
              or Decimal(str(registered["dividend_per_current_share_cny"])) != amount):
            mismatched.append(ex_date)
    # Flag stale secondary lists too: registry-only events are not proof of
    # issuer completeness and must not be silently excluded from coverage.
    unlisted_registry = sorted(
        event["event_date"] for event in events
        if event["type"] == "CASH_DIVIDEND"
        and event["symbol"] == symbol
        and event["event_date"] >= start_date
        and event["event_date"] not in candidate_dates_in_window
    )
    return {
        "scope_symbol": symbol,
        "research_window_start": start_date,
        "candidate_events_in_window": checked,
        "missing_from_executable_registry": missing,
        "conflicting_registry_events": mismatched,
        "registry_events_absent_from_candidate_list": unlisted_registry,
        "coverage_discrepancy": bool(missing or mismatched or unlisted_registry),
        "source_grade": "SECONDARY_CANDIDATES_NOT_PRIMARY_ISSUER_VERIFICATION",
        "status": "REVIEW_REQUIRED_NOT_FOR_TRADING",
        "warning": "Coverage of this one symbol is not completeness for all eight ETFs; no prices, broker cash, or strategy results were certified.",
    }

def main():
    if not REGISTRY.is_file() or not PRICES.is_file():
        raise SystemExit("Real corporate-action registry and Tushare prices.csv required")
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    with PRICES.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    diagnostics = analyze(registry, rows)
    OUT.mkdir(parents=True, exist_ok=True)
    detail = OUT / "corporate_action_factor_diagnostics.csv"
    with detail.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(diagnostics[0]))
        writer.writeheader()
        writer.writerows(diagnostics)
    candidate_file = ROOT / "metadata" / "510300_dividend_coverage_gap_20261024.json"
    candidate_report = None
    if candidate_file.is_file():
        candidates = json.loads(candidate_file.read_text(encoding="utf-8"))
        candidate_report = candidate_dividend_coverage(registry, candidates)
        (OUT / "corporate_action_candidate_coverage.json").write_text(
            json.dumps(candidate_report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    result = {
        "status": "REVIEW_REQUIRED_NOT_FOR_TRADING",
        "events_diagnosed": len(diagnostics),
        "secondary_candidate_coverage": candidate_report,
        "events_needing_manual_review": sum(x["needs_manual_review"] for x in diagnostics),
        "source_registry_sha256": hashlib.sha256(REGISTRY.read_bytes()).hexdigest(),
        "price_input_sha256": hashlib.sha256(PRICES.read_bytes()).hexdigest(),
        "diagnostics_sha256": hashlib.sha256(detail.read_bytes()).hexdigest(),
        "limitations": [
            "Matching fund_adj ratios are not independent confirmation of cash total return.",
            "A fund split changes held economic units; adjusted-price backtests use synthetic units.",
            "Dividend entitlement on record date and cash settlement on payment date require separate ledger accounting.",
            "Quotes bracketing an event also contain actual market movement and possible other actions.",
            "No original prices or trading signals were modified; historical performance not recertified.",
        ],
        "next_action": "Review discrepancies against issuer/exchange and independent prices; then implement dated unit/cash receivable ledger.",
    }
    (OUT / "corporate_action_factor_summary.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print("ETF_CORPORATE_ACTION_DIAGNOSTIC " + json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
