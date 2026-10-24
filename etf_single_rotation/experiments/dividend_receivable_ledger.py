"""Pure, non-trading corporate-action accounting helpers (NOT_FOR_TRADING).

Does not reconstruct holdings or certify fund_adj. Caller must supply an
explicit record-date share snapshot for each cash event; missing fails closed.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
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


def _whole_units(value, label):
    """Fail closed on fractional, boolean, or imprecisely represented ETF units."""
    if isinstance(value, bool):
        raise ValueError(f"Invalid {label}")
    # Validate the original decimal representation BEFORE converting to float.
    # float(Decimal("10.0000000000000001")) == 10.0 would otherwise
    # silently accept fractional registered shares.
    try:
        units = Decimal(str(value))
    except (ValueError, TypeError, ArithmeticError) as exc:
        raise ValueError(f"Invalid {label}") from exc
    if not units.is_finite() or units < 0:
        raise ValueError(f"Invalid {label}")
    if units > 2**53 - 1:
        raise ValueError(f"Unsafe {label}: exceeds exact integer range")
    if units != units.to_integral_value():
        raise ValueError(f"{label} must be whole units")
    return int(units)


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
        shares = _whole_units(record_date_shares[key], "record-date shares")
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


def record_date_snapshots_from_fills(registry, opening_date, opening_positions, fills):
    """Derive end-of-record-date holdings from an explicit ordered fill ledger.

    Synthetic/research accounting only. opening_positions are shares at the
    *end* of opening_date; all fills must occur strictly later. Same-day fills
    must be supplied in actual execution order. No prices, brokerage settlement,
    or market-data certification is implied. Enforces a single ETF after each ordered execution and unit event.
    """
    events = validate_events(registry)
    record_dates = sorted({
        (event["symbol"], _day(event["registration_date"]))
        for event in events if event["type"] == "CASH_DIVIDEND"
    }, key=lambda pair: (pair[1], pair[0]))
    if not record_dates:
        raise ValueError("No cash-dividend record dates")
    start = _day(opening_date)
    if start >= record_dates[0][1]:
        raise ValueError("Opening date must precede earliest record date")
    allowed = {
        "510300.SH", "510500.SH", "159915.SZ", "510050.SH",
        "510880.SH", "518880.SH", "512100.SH", "512690.SH",
    }
    positions = {}
    for symbol, shares in opening_positions.items():
        if symbol not in allowed:
            raise ValueError("Unexpected ETF in opening positions")
        amount = _whole_units(shares, "opening shares")
        positions[symbol] = amount
    if sum(v > 0 for v in positions.values()) > 1:
        raise ValueError("More than one ETF held at opening")
    prior_day = start
    normalized = []
    for fill in fills:
        if not isinstance(fill, dict):
            raise ValueError("Invalid fill")
        symbol = fill.get("symbol")
        if symbol not in allowed:
            raise ValueError("Unexpected ETF in fill")
        day = _day(fill.get("date"))
        if day <= start or day < prior_day:
            raise ValueError("Fills must be chronological and after opening")
        prior_day = day
        side = fill.get("side")
        if side not in ("BUY", "SELL"):
            raise ValueError("Invalid fill side")
        shares = _whole_units(fill.get("shares"), "fill shares")
        if shares <= 0:
            raise ValueError("Fill shares must be positive whole units")
        normalized.append((day, symbol, side, int(shares)))
    # Opening positions are end-of-day: events on/before opening_date are
    # already reflected in the supplied shares. Apply later unit events exactly
    # once before computing record-date entitlements. Never infer brokerage
    # registration rounding for consolidations.
    last_record_day = record_dates[-1][1]
    # Reject fills outside the entitlement snapshot window; otherwise later
    # executions silently evade the single-position and no-oversell checks.
    if normalized and normalized[-1][0] > last_record_day:
        raise ValueError("Fill after final record date is outside snapshot scope")
    unit_events = [
        event for event in events
        if event["type"] in ("UNIT_SPLIT", "UNIT_CONSOLIDATION")
        and start < _day(event["event_date"]) <= last_record_day
    ]
    fill_keys = {(day, symbol) for day, symbol, _, _ in normalized}
    for event in unit_events:
        if (_day(event["event_date"]), event["symbol"]) in fill_keys:
            raise ValueError("Ambiguous same-day corporate event and fill")
    timeline = [(day, 1, symbol, side, shares, None)
                for day, symbol, side, shares in normalized]
    timeline.extend(
        (_day(event["event_date"]), 0, event["symbol"], "UNIT_EVENT", 0, event)
        for event in unit_events
    )
    timeline.sort(key=lambda item: (item[0], item[1]))

    snapshots = {}
    index = 0
    for symbol, record_day in record_dates:
        while index < len(timeline) and timeline[index][0] <= record_day:
            day = timeline[index][0]
            while index < len(timeline) and timeline[index][0] == day:
                _, _, traded, side, shares, event = timeline[index]
                if side == "UNIT_EVENT":
                    held = positions.get(traded, 0)
                    if held:
                        if event["type"] == "UNIT_CONSOLIDATION":
                            raise ValueError(
                                "Held consolidation requires issuer-specific "
                                "registered-unit rounding and account evidence"
                            )
                        converted = Decimal(held) * Decimal(
                            str(event["new_units_per_old_unit"])
                        )
                        if converted != converted.to_integral_value():
                            raise ValueError(
                                "Split creates fractional registered units; "
                                "issuer rounding is unverified"
                            )
                        positions[traded] = _whole_units(int(converted), "post-split shares")
                else:
                    positions[traded] = positions.get(traded, 0) + (
                        shares if side == "BUY" else -shares
                    )
                    if positions[traded] < 0:
                        raise ValueError("Sell exceeds held ETF shares")
                    # Individually valid fills can overflow the exact integer
                    # limit after accumulation. Never emit an unsafe snapshot.
                    positions[traded] = _whole_units(positions[traded], "resulting shares")
                # The one-ETF mandate applies after EACH execution, not
                # merely at the close; an intraday overlap cannot be hidden
                # by a later sale on the same date.
                if sum(v > 0 for v in positions.values()) > 1:
                    raise ValueError("More than one ETF held at any execution step")
                index += 1
        snapshots[(symbol, record_day.isoformat())] = positions.get(symbol, 0)
    return snapshots
