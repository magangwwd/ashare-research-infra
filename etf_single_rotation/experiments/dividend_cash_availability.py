"""Research-only separation of cash paid and cash actually spendable.

The existing entitlement ledger records issuer payment dates, NOT investor
cash availability. An independently verified availability date is mandatory
for every positive entitlement. No broker assumptions, trades or price edits.
NOT_FOR_TRADING.
"""
from __future__ import annotations

from datetime import date
from math import isfinite
import re

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


def _date_with_evidence_metadata(value):
    """Reject unsubstantiated date strings; metadata is NOT proof of settlement.

    The caller must independently authenticate the referenced private
    broker/CSDC evidence and verify its SHA256 outside this helper. Do not
    commit account statements, personal identifiers, or private URLs.
    """
    if not isinstance(value, dict) or set(value) != {
        "available_date", "evidence_reference", "evidence_sha256"
    }:
        raise ValueError("Cash availability requires dated evidence metadata")
    reference = value["evidence_reference"]
    digest = value["evidence_sha256"]
    if not isinstance(reference, str) or not reference.strip():
        raise ValueError("Cash availability evidence reference is missing")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("Cash availability evidence SHA256 must be 64 lowercase hex digits")
    return _date(value["available_date"])


def cash_availability_ledger(registry, record_date_shares, observation_dates, verified_available_dates):
    """Require verified dates keyed by (symbol, registration_date).

    Paid cash is not assumed spendable on the issuer payment date.
    Each positive entitlement requires a structured availability date plus
    a non-sensitive evidence reference and SHA256. A bare issuer payment date
    or date string is not sufficient. This checks metadata shape only; it
    does NOT independently authenticate a broker/CSDC statement or its hash.
    Zero entitlements need no settlement evidence.
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
        available = _date_with_evidence_metadata(verified_available_dates[key])
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
        "warning": "Payment date is not spendable cash. Evidence metadata is caller-supplied and NOT independently authenticated by this helper; no trading use.",
    }
