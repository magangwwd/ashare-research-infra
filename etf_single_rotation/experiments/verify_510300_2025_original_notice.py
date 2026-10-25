"""Fail-closed, offline original dividend notice metadata crosscheck.

This does NOT download/verify PDF bytes, confirm broker cash or modify trades.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse


def _date(value, label):
    if not isinstance(value, str):
        raise ValueError("Invalid " + label)
    try:
        d = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Invalid " + label) from exc
    if d.isoformat() != value:
        raise ValueError("Noncanonical " + label)
    return d


def _amount(value):
    if not isinstance(value, str):
        raise ValueError("Invalid dividend amount")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Invalid dividend amount") from exc
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Invalid dividend amount")
    return amount


def validate_official_notice_link(note, candidates):
    """Validate source identity, candidate dates/amount, and non-trading gates."""
    if not isinstance(note, dict) or not isinstance(candidates, dict):
        raise ValueError("Expected dictionaries")
    if (note.get("schema_version"), note.get("project"), note.get("task_id"),
            note.get("symbol"), note.get("evidence_role")) != (
            1, "ETF_SINGLE_ROTATION", "ETF-DIV-004", "510300.SH",
            "OFFICIAL_ORIGINAL_NOTICE_LINK_TEXT_ONLY"):
        raise ValueError("Wrong source identity")
    source = note.get("source")
    if not isinstance(source, dict):
        raise ValueError("Missing official source")
    url = source.get("url", "")
    if not isinstance(url, str):
        raise ValueError("Invalid official URL")
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.hostname != "www.sse.com.cn"
            or parsed.username is not None or parsed.password is not None
            or parsed.port is not None or parsed.query or parsed.fragment
            or parsed.path != "/disclosure/fund/announcement/c/new/2025-06-11/510300_20250611_ZAU4.pdf"):
        raise ValueError("Not the verified official SSE URL")
    if (source.get("notice_date") != "2025-06-11"
            or source.get("pdf_pages") != 3
            or source.get("official_pdf_text_inspected") is not True):
        raise ValueError("Unreviewed official notice text")
    if (source.get("original_pdf_sha256") is not None
            or source.get("original_pdf_bytes_readback_verified") is not False):
        raise ValueError("Cannot claim original PDF byte verification")
    announced = note.get("announced_distribution")
    if not isinstance(announced, dict):
        raise ValueError("Missing announced distribution")
    expected = {
        "record_date": "2025-06-17",
        "ex_dividend_date": "2025-06-18",
        "announced_payment_date": "2025-06-27",
    }
    _date(source["notice_date"], "notice date")
    for key, val in expected.items():
        if announced.get(key) != val:
            raise ValueError("Official notice " + key + " mismatch")
        _date(announced[key], key)
    per10 = _amount(announced.get("dividend_per_10_shares_cny"))
    if per10 != Decimal("0.8800"):
        raise ValueError("Official notice dividend amount mismatch")
    acceptance = note.get("acceptance")
    if not isinstance(acceptance, dict):
        raise ValueError("Missing research gate")
    if acceptance.get("status") != "REVIEW_REQUIRED" or acceptance.get("trading_status") != "NOT_FOR_TRADING":
        raise ValueError("Research gate must remain closed")
    for flag in ("independent_market_prices_verified", "broker_spendable_cash_verified",
                 "drive_write_readback_verified", "executable_event_registry_updated"):
        if acceptance.get(flag) is not False:
            raise ValueError("Unverified downstream acceptance: " + flag)
    if candidates.get("symbol") != "510300.SH" or candidates.get("schema_version") != 1:
        raise ValueError("Wrong candidate series")
    events = candidates.get("events")
    if not isinstance(events, list):
        raise ValueError("Missing candidate events")
    rows = [row for row in events if isinstance(row, dict)
            and row.get("ex_dividend_date") == expected["ex_dividend_date"]]
    if len(rows) != 1:
        raise ValueError("Missing or duplicate 2025 candidate")
    row = rows[0]
    for k, v in (("registration_date", expected["record_date"]),
                 ("ex_dividend_date", expected["ex_dividend_date"]),
                 ("announced_payment_date", expected["announced_payment_date"])):
        if row.get(k) != v:
            raise ValueError("Candidate " + k + " mismatch")
        _date(row[k], k)
    if _amount(row.get("dividend_per_share_cny")) * 10 != per10:
        raise ValueError("Candidate dividend amount mismatch")
    return {
        "task_id": "ETF-DIV-004", "symbol": "510300.SH",
        "source": "OFFICIAL_SSE_ORIGINAL_NOTICE_TEXT",
        "record_date": expected["record_date"],
        "ex_dividend_date": expected["ex_dividend_date"],
        "announced_payment_date": expected["announced_payment_date"],
        "dividend_per_share_cny": str(per10 / 10),
        "original_pdf_bytes_verified": False,
        "broker_cash_verified": False,
        "drive_readback_verified": False,
        "status": "REVIEW_REQUIRED", "trading_status": "NOT_FOR_TRADING",
    }
