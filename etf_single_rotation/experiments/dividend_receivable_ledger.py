"""Pure, non-trading corporate-action accounting helpers (NOT_FOR_TRADING).

Does not reconstruct holdings or certify fund_adj. Caller must supply an
explicit record-date share snapshot for each cash event; missing fails closed.
"""
from __future__ import annotations

from datetime import date
from math import isfinite

from etf_single_rotation.experiments.audit_corporate_actions import validate_events


def _day(value):
    if not isinstance(value, str):
        raise ValueError("Expected ISO date string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Invalid ISO date") from exc
    if parsed.isoformat() != value:
        raise ValueError("Noncanonical ISO date")
    return parsed


def _nonnegative(value, label):
    try:
        number = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid {label}") from exc
    if not isfinite(number) or number < 0:
        raise ValueError(f"Invalid {label}")
    return number


def apply_unit_change(shares, event):
    """Return post-split/consolidation economic units; no synthetic cash."""
    units = _nonnegative(shares, "held shares")
    kind = event.get("type")
    if kind not in ("UNIT_SPLIT", "UNIT_CONSOLIDATION"):
        raise ValueError("Expected a unit-changing event")
    validate_events({"schema_version": 1, "events": [event]})
    result = units * float(event["new_units_per_old_unit"])
    if not isfinite(result):
        raise ValueError("Overflow in unit conversion")
    return result


def cash_entitlement_ledger(registry, record_date_shares, observation_dates):
    """Lock entitlement on registration; recognize NAV receivable on ex-date; settle on payment.

    record_date_shares: {(symbol, 'YYYY-MM-DD'): nonnegative shares}.
    Every dividend needs an explicit snapshot, including zero holdings.
    observation_dates: ascending unique ISO dates. No prices are used; NAV treatment assumes unadjusted raw prices and must not be combined with adjusted prices.
    """
    events = validate_events(registry)
    days = [_day(x) for x in observation_dates]
    if not days or days != sorted(set(days)):
        raise ValueError("Observation dates must be nonempty, unique and ordered")
    details = []
    for event in events:
        if event["type"] != "CASH_DIVIDEND":
            continue
        key = (event["symbol"], event["registration_date"])
        if key not in record_date_shares:
            raise ValueError(f"Missing record-date share snapshot: {key}")
        shares = _nonnegative(record_date_shares[key], "record-date shares")
        amount = shares * float(event["dividend_per_current_share_cny"])
        if not isfinite(amount):
            raise ValueError("Overflow in cash entitlement")
        details.append({
            "symbol": event["symbol"],
            "registration_date": event["registration_date"],
            "ex_dividend_date": event["event_date"],
            "payment_date": event["cash_payment_date"],
            "record_date_shares": shares,
            "cash_entitlement_cny": amount,
            "source_url": event["source_url"],
            "status": "RESEARCH_ACCOUNTING_NOT_PRICE_CERTIFICATION",
        })
    rows = []
    for day in days:
        locked = receivable = paid = 0.0
        for item in details:
            registration = _day(item["registration_date"])
            ex_date = _day(item["ex_dividend_date"])
            payment = _day(item["payment_date"])
            amount = item["cash_entitlement_cny"]
            # Record-date entitlement is informational, not NAV cash.
            # With raw (unadjusted) prices, recognize receivable at ex-date,
            # when the price becomes ex-dividend, to avoid double counting.
            if registration <= day < payment:
                locked += amount
            if ex_date <= day < payment:
                receivable += amount
            elif day >= payment:
                paid += amount
        rows.append({
            "date": day.isoformat(),
            "record_date_locked_entitlement_cny": locked,
            "cash_receivable_cny": receivable,
            "cumulative_cash_paid_cny": paid,
            "status": "RESEARCH_ACCOUNTING_NOT_TOTAL_RETURN_CERTIFICATION",
        })
    return {"events": details, "daily": rows, "status": "REVIEW_REQUIRED_NOT_FOR_TRADING"}
