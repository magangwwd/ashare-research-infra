"""Issuer-specific, read-only registered share conversion for 510500.SH.

Issuer's 2015-04-15 result notice: 2015-04-13 record date, 2015-04-14
registration change, ratio 0.28032483; each holder's converted units
are rounded DOWN to whole shares and the discarded fraction goes to fund
assets. This is not a brokerage account statement or market-price audit.

Original issuer announcement reproduced at:
https://pdf.dfcfw.com/pdf/H2_AN201504150009203406_1.pdf
Research only. NOT_FOR_TRADING.
"""
from __future__ import annotations

SYMBOL = "510500.SH"
EFFECTIVE_DATE = "2015-04-14"
PUBLISHED_RATIO = "0.28032483"
RATIO_NUMERATOR = 28032483
RATIO_DENOMINATOR = 100000000
ROUNDING_RULE = "FLOOR_TO_WHOLE_SHARE_FRACTION_TO_FUND"


def registered_consolidation_510500(
    shares: int, *, symbol: str = SYMBOL,
    event_date: str = EFFECTIVE_DATE,
    published_ratio: str = PUBLISHED_RATIO,
    rounding_rule: str = ROUNDING_RULE,
) -> dict:
    """Compute a theoretical per-holder register, never aggregate first.

    The issuer's ratio is published to eight decimal places; the result
    is a theoretical check, not independently verified actual CSDC units.
    No cash amount, price return or broker action is inferred.
    """
    if (symbol != SYMBOL or event_date != EFFECTIVE_DATE
            or published_ratio != PUBLISHED_RATIO
            or rounding_rule != ROUNDING_RULE):
        raise ValueError("Unverified 510500 event or published ratio/rounding rule")
    if isinstance(shares, bool) or not isinstance(shares, int) or shares < 0:
        raise ValueError("Registered pre-event units must be a nonnegative integer")

    product = shares * RATIO_NUMERATOR
    whole, remainder = divmod(product, RATIO_DENOMINATOR)
    return {
        "symbol": SYMBOL,
        "effective_date": EFFECTIVE_DATE,
        "pre_registered_units": shares,
        "published_ratio": PUBLISHED_RATIO,
        "rounding_rule": ROUNDING_RULE,
        "fractional_units_at_published_ratio": f"{whole}.{remainder:08d}",
        "theoretical_post_registered_units": whole,
        "theoretical_discarded_fractional_units": f"0.{remainder:08d}",
        "cash_compensation_cny": None,
        "status": "REVIEW_REQUIRED_NOT_FOR_TRADING",
    }
