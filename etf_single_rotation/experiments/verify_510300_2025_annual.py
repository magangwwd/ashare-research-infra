"""Read-only 510300 2025 issuer annual-report dividend crosscheck.

The issuer annual report corroborates announcement date, record date and
dividend amount, NOT the ex-date, broker settlement, or total-return prices.
No executable event registry, strategy or portfolio is changed.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse


def _date(value, label):
    if not isinstance(value, str):
        raise ValueError("Invalid " + label)
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Invalid " + label) from exc
    if parsed.isoformat() != value:
        raise ValueError("Noncanonical " + label)
    return parsed


def _amount(value, label):
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError("Invalid " + label)
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Invalid " + label) from exc
    if not result.is_finite() or result <= 0:
        raise ValueError("Invalid " + label)
    return result


def validate_annual_crosscheck(source, candidates):
    """Fail closed on conflicting 2025 source/candidate facts.

    Never infer an actual cash payment or independent price confirmation.
    """
    if source.get("task_id") != "ETF-DIV-004" or source.get("symbol") != "510300.SH":
        raise ValueError("Wrong source identity")
    if source.get("status") != "REVIEW_REQUIRED" or source.get("trading_status") != "NOT_FOR_TRADING":
        raise ValueError("Research gate must remain closed")
    if source.get("evidence_role") != "PRIMARY_ANNUAL_REPORT_CORROBORATION_ONLY":
        raise ValueError("Wrong evidence role")
    if source.get("source_pdf_bytes_sha256") is not None or source.get("pdf_text_machine_verified") is not False:
        raise ValueError("Unverified PDF bytes/text cannot be promoted")
    for flag in ("independent_market_prices_verified", "broker_spendable_cash_verified", "drive_readback_performed"):
        if source.get(flag) is not False:
            raise ValueError("Unverified downstream acceptance: " + flag)
    annual = source.get("issuer_annual_report")
    if not isinstance(annual, dict):
        raise ValueError("Missing issuer annual report")
    parsed = urlparse(annual.get("url", ""))
    if parsed.scheme != "https" or parsed.hostname != "www.sse.com.cn":
        raise ValueError("Not an official SSE annual report URL")
    if annual.get("report_year") != 2025 or annual.get("published_date") != "2026-03-31":
        raise ValueError("Wrong annual report")
    _date(annual["published_date"], "report published date")
    if annual.get("notice_date") != "2025-06-11":
        raise ValueError("Wrong annual report announcement date")
    if annual.get("record_date") != "2025-06-17":
        raise ValueError("Wrong annual report record date")
    _date(annual["notice_date"], "notice date")
    _date(annual["record_date"], "record date")
    if annual.get("distribution_count_for_2025") != 1:
        raise ValueError("Annual report distribution count not confirmed")
    per_10 = _amount(annual.get("dividend_per_10_shares_cny"), "per-10 dividend")
    if candidates.get("symbol") != "510300.SH" or candidates.get("schema_version") != 1:
        raise ValueError("Wrong candidate series")
    rows = [row for row in candidates.get("events", [])
            if row.get("ex_dividend_date") == "2025-06-18"]
    if len(rows) != 1:
        raise ValueError("Missing or duplicate 2025 candidate")
    row = rows[0]
    for field in ("registration_date", "ex_dividend_date", "announced_payment_date"):
        _date(row.get(field), field)
    if row["registration_date"] != annual["record_date"]:
        raise ValueError("Record date conflict")
    if per_10 / Decimal("10") != _amount(row.get("dividend_per_share_cny"), "per-share dividend"):
        raise ValueError("Dividend amount conflict")
    # Annual report does not independently verify the ex/payment dates.
    # The separately linked SSE option notice corroborates the ex-date only.
    corroboration = source.get("sse_option_ex_date")
    if not isinstance(corroboration, dict):
        raise ValueError("Missing exchange ex-date corroboration")
    u = urlparse(corroboration.get("url", ""))
    if u.scheme != "https" or u.hostname not in ("www.sse.com.cn", "big5.sse.com.cn"):
        raise ValueError("Non-official option notice")
    if corroboration.get("ex_dividend_date") != row["ex_dividend_date"]:
        raise ValueError("Ex-date conflict")
    return {
        "task_id": "ETF-DIV-004",
        "symbol": "510300.SH",
        "notice_date": annual["notice_date"],
        "record_date": annual["record_date"],
        "ex_dividend_date": row["ex_dividend_date"],
        "dividend_per_share_cny": str(per_10 / Decimal("10")),
        "announced_payment_date": row["announced_payment_date"],
        "status": "REVIEW_REQUIRED",
        "trading_status": "NOT_FOR_TRADING",
        "annual_report_corrob_amount_and_record": True,
        "annual_report_confirms_ex_date_or_payment": False,
        "broker_cash_verified": False,
        "independent_market_prices_verified": False,
        "drive_readback_performed": False,
    }
