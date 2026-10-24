"""Issuer-specific *research-only* registered unit conversion for 512100.SH.

This is a per-holder theoretical check using the issuer's published rounded
ratio. It does not reconstruct actual CSDC registers, portfolio cash, market
prices, or total returns. It MUST NOT be used for trading. NOT_FOR_TRADING.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_CEILING

SYMBOL = "512100.SH"
EFFECTIVE_DATE = "2022-09-02"
PUBLISHED_RATIO = Decimal("0.36555")
ROUNDING_RULE = "CEILING_TO_WHOLE_SHARE_MANAGER_FUNDED"


def registered_consolidation_512100(shares, *, symbol=SYMBOL, event_date=EFFECTIVE_DATE,
                                   published_ratio="0.36555", rounding_rule=ROUNDING_RULE):
    """Calculate theoretical post-consolidation registered whole units.

    Per-account upward rounding is different from portfolio-economic fractional
    units. Published ratio precision is limited; do not infer cash subsidies.
    """
    if symbol != SYMBOL or event_date != EFFECTIVE_DATE or rounding_rule != ROUNDING_RULE:
        raise ValueError("Unverified consolidation event or rounding rule")
    if isinstance(shares, bool) or not isinstance(shares, int) or shares < 0:
        raise ValueError("Registered pre-event units must be a nonnegative integer")
    try:
        ratio = Decimal(published_ratio)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Invalid published consolidation ratio") from exc
    if not ratio.is_finite() or ratio != PUBLISHED_RATIO:
        raise ValueError("Unverified published consolidation ratio")
    fractional = Decimal(shares) * ratio
    registered = int(fractional.to_integral_value(rounding=ROUND_CEILING))
    return {
        "symbol": symbol,
        "event_date": event_date,
        "pre_registered_units": shares,
        "theoretical_post_registered_units": registered,
        "fractional_units_at_published_ratio": str(fractional),
        "theoretical_rounding_increment_units": str(Decimal(registered) - fractional),
        "ratio_is_published_rounded_value": True,
        "cash_subsidy_cny": None,
        "status": "REVIEW_REQUIRED_NOT_FOR_TRADING",
    }
