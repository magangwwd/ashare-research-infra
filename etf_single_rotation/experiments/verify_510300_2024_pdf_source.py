"""Pinned read-only 2024 SSE issuer dividend source gate; not a price/cash audit."""
from datetime import date
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

EXPECTED_URL = (
    "https://www.sse.com.cn/disclosure/fund/announcement/c/new/"
    "2024-01-11/510300_20240111_QQFV.pdf"
)
DATES = {
    "registration_date": "2024-01-17",
    "ex_dividend_date": "2024-01-18",
    "announced_payment_date": "2024-01-23",
}


def _canonical_date(value):
    if not isinstance(value, str):
        raise ValueError("Invalid date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Invalid date") from exc
    if parsed.isoformat() != value:
        raise ValueError("Noncanonical date")
    return parsed


def _money(value):
    if not isinstance(value, str):
        raise ValueError("Invalid amount")
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Invalid amount") from exc
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Invalid amount")
    return amount


def validate_2024_source(meta, candidates):
    """Only corroborate the already-inspected issuer notice against candidates."""
    if not isinstance(meta, dict) or not isinstance(candidates, dict):
        raise ValueError("Invalid evidence container")
    if (meta.get("schema_version"), meta.get("task_id"), meta.get("symbol"),
            meta.get("research_only"), meta.get("trading_status"), meta.get("status")) != (
            1, "ETF-DIV-004", "510300.SH", True, "NOT_FOR_TRADING", "REVIEW_REQUIRED"):
        raise ValueError("Unsafe evidence identity or acceptance")
    source = meta.get("source")
    if not isinstance(source, dict):
        raise ValueError("Missing official source")
    url = source.get("url")
    if not isinstance(url, str) or url != EXPECTED_URL:
        raise ValueError("Unapproved official source URL")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != "www.sse.com.cn"
            or parsed.username or parsed.password or parsed.port is not None
            or parsed.query or parsed.fragment):
        raise ValueError("Invalid official source URL")
    if (source.get("notice_date") != "2024-01-11"
            or source.get("pdf_pages") != 3
            or source.get("pdf_downloaded") is not False
            or source.get("pdf_sha256") is not None):
        raise ValueError("Unverified PDF bytes must not be promoted")
    event = meta.get("event")
    if not isinstance(event, dict):
        raise ValueError("Missing issuer event")
    for key, expected in DATES.items():
        if event.get(key) != expected:
            raise ValueError("Issuer event date mismatch: " + key)
        _canonical_date(event[key])
    if (_money(event.get("dividend_per_10_shares_cny")) != Decimal("0.6900")
            or _money(event.get("dividend_per_share_cny")) != Decimal("0.0690")):
        raise ValueError("Issuer dividend amount mismatch")
    if (_money(event["dividend_per_share_cny"]) * 10
            != _money(event["dividend_per_10_shares_cny"])):
        raise ValueError("Per-share dividend unit mismatch")
    if (event.get("issuer_to_csd_date") != "2024-01-19"
            or event.get("csd_to_broker_date") != "2024-01-22"
            or event.get("investor_cash_available_date") is not None):
        raise ValueError("Unverified investor cash or transfer schedule")
    if not (_canonical_date(DATES["ex_dividend_date"])
            < _canonical_date(event["issuer_to_csd_date"])
            <= _canonical_date(event["csd_to_broker_date"])
            < _canonical_date(DATES["announced_payment_date"])):
        raise ValueError("Impossible transfer chronology")
    crosscheck = meta.get("crosscheck")
    if (not isinstance(crosscheck, dict)
            or crosscheck.get("factor_difference_is_not_independent_price_verification") is not True):
        raise ValueError("Cannot promote same-source factors to independent quotes")
    if candidates.get("schema_version") != 1 or candidates.get("symbol") != "510300.SH":
        raise ValueError("Wrong candidate universe")
    rows = candidates.get("events")
    if not isinstance(rows, list):
        raise ValueError("Missing candidates")
    matches = [x for x in rows if isinstance(x, dict)
               and x.get("ex_dividend_date") == DATES["ex_dividend_date"]]
    if len(matches) != 1:
        raise ValueError("Missing or duplicate 2024 dividend candidate")
    candidate = matches[0]
    for left, right in (("registration_date", "registration_date"),
                        ("ex_dividend_date", "ex_dividend_date"),
                        ("announced_payment_date", "announced_payment_date")):
        if candidate.get(left) != event[right]:
            raise ValueError("Candidate date conflict")
        _canonical_date(candidate[left])
    if _money(candidate.get("dividend_per_share_cny")) != Decimal("0.0690"):
        raise ValueError("Candidate cash amount conflict")
    return {
        "task_id": "ETF-DIV-004", "symbol": "510300.SH",
        "source": EXPECTED_URL, "ex_dividend_date": "2024-01-18",
        "amount_cny_per_share": "0.0690",
        "source_pdf_bytes_verified": False,
        "independent_market_price_verified": False,
        "investor_cash_verified": False,
        "drive_readback_verified": False,
        "status": "REVIEW_REQUIRED", "trading_status": "NOT_FOR_TRADING",
    }
