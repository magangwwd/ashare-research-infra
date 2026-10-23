"""Research-only separation of cash paid and cash actually spendable.

The existing entitlement ledger records issuer payment dates, NOT investor
cash availability. An independently verified availability date is mandatory
for every positive entitlement. No broker assumptions, trades or price edits.
NOT_FOR_TRADING.
"""
from __future__ import annotations

from datetime import date
from math import isfinite

from etf_single_rotation.experiments.dividend_receivable_ledger import (
    cash_entitlement_ledger,
)


def _date(value):
    if not isinstance(value, str):
        raise ValueError("Availability date must be an ISO date string")
    try:
        day = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Invalid availability date") from exc
    if day.isoformat() != value:
        raise ValueError("Noncanonical availability date")
    return day


def cash_availability_ledger(registry, record_date_shares, observation_dates, verified_available_dates):
    """Require verified dates keyed by (symbol, registration_date).

    Paid cash is not assumed spendable on the issuer payment date.
    Missing independent availability evidence fails closed, including when
    the nominal issuer payment date has already passed. Zero entitlements
    require no settlement evidence because no cash is owed.
    """
    if not isinstance(verified_available_dates, dict):
        raise ValueError("Verified availability dates must be a mapping")
    base = cash_entitlement_ledger(registry, record_date_shares, observation_dates)
    availability = []
    for event in base["events"]:
        amount = event["cash_entitlement_cny"]
        if not isfinite(amount) or amount < 0:
            raise ValueError("Invalid cash entitlement")
        if amount == 0:
            continue
        key = (event["symbol"], event["registration_date"])
        if key not in verified_available_dates:
            raise ValueError(f"Missing verified cash availability date: {key}")
        available = _date(verified_available_dates[key])
        payment = _date(event["payment_date"])
        if available < payment:
            raise ValueError("Cash availability cannot precede issuer payment")
        availability.append((available, amount))
    rows = []
    for row in base["daily"]:
        day = _date(row["date"])
        available = sum(amount for when, amount in availability if when <= day)
        paid = row["cumulative_cash_paid_cny"]
        unavailable = paid - available
        if unavailable < -1e-9:
            raise ValueError("Cash availability exceeds paid cash")
        rows.append({
            **row,
            "cumulative_verified_available_cash_cny": available,
            "paid_but_not_verified_available_cny": max(0.0, unavailable),
            "status": "RESEARCH_ONLY_AVAILABILITY_EVIDENCE_REQUIRED",
        })
    return {
        "events": base["events"],
        "daily": rows,
        "status": "REVIEW_REQUIRED_NOT_FOR_TRADING",
        "warning": "Payment date is not assumed to be investor cash availability; no trading use.",
    }
