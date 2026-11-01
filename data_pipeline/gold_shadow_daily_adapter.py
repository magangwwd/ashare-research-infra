"""Offline, shadow-only Task01 daily -> Gold daily_prices row adapter.

This module cannot publish, certify historical universe membership, or authorize a
production switch. Gold instrument_id must come from an explicit symbol mapping;
Task01 security_id is a different UUID5 namespace and is NOT interchangeable.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN


_REQUIRED = frozenset({
    "ts_code", "security_id", "trade_date", "open", "high", "low", "close",
    "pre_close", "change", "pct_chg", "vol", "amount",
})


def _number(value, name):
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{name}: invalid numeric value")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{name}: invalid numeric value") from None
    if not number.is_finite():
        raise ValueError(f"{name}: nonfinite numeric value")
    return number


def _exact_units(value, multiplier, name):
    number = _number(value, name)
    if number < 0:
        raise ValueError(f"{name}: negative value")
    scaled = number * multiplier
    if scaled != scaled.to_integral_value() or scaled > 9223372036854775807:
        raise ValueError(f"{name}: nonintegral or overflowing converted units")
    return int(scaled)


def _shadow_turnover_cny(amount):
    """Return nearest-integer CNY shadow candidate, plus precision-loss flag.

    A real Oct 2026 sample showed that Task01 can represent fractional yuan
    while Gold stores integer yuan. This is a deliberately UNVALIDATED
    hypothesis, not proof of the Gold producer's historical rounding contract.
    """
    scaled = _number(amount, "amount") * Decimal("1000")
    if scaled < 0:
        raise ValueError("amount: negative value")
    rounded = scaled.quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
    if rounded > 9223372036854775807:
        raise ValueError("amount: overflowing converted units")
    return int(rounded), scaled != rounded


def _date(value, name):
    if isinstance(value, date) and not hasattr(value, "hour"):
        return value
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError(f"{name}: expected ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name}: invalid ISO date") from None


def adapt_daily_shadow(rows, instruments, *, task_as_of, instruments_as_of):
    """Return (candidate rows, audit); all candidates are deliberately unvalidated.

    Inputs must be in-memory copies of separately verified Parquet files. The
    caller must verify original Drive file hashes and historical point-in-time
    membership separately. No filesystem, network, or production side effects.
    """
    cutoff = _date(task_as_of, "task_as_of")
    if _date(instruments_as_of, "instruments_as_of") != cutoff:
        raise ValueError("PIT_UNIVERSE_DATE_MISMATCH: no future Gold universe")
    if not isinstance(rows, list) or not isinstance(instruments, list):
        raise ValueError("rows and instruments must be lists")

    by_symbol = {}
    ids = set()
    for item in instruments:
        if not isinstance(item, dict):
            raise ValueError("invalid instrument record")
        symbol, ident = item.get("vendor_symbol"), item.get("instrument_id")
        if not isinstance(symbol, str) or not symbol or not isinstance(ident, str) or not ident:
            raise ValueError("missing Gold symbol or instrument_id")
        # Do not trust the caller's date label alone. Verify each observed Gold row.
        # Current Gold instrument records have as_of_date; a future snapshot
        # cannot be passed as a historical PIT universe by relabeling metadata.
        if _date(item.get("as_of_date"), "instrument.as_of_date") != cutoff:
            raise ValueError("PIT_UNIVERSE_ROW_DATE_MISMATCH: instrument date differs")
        if symbol in by_symbol or ident in ids:
            raise ValueError("duplicate Gold symbol or instrument_id")
        by_symbol[symbol] = ident
        ids.add(ident)

    out, seen, excluded, turnover_rounded_rows = [], set(), 0, 0
    # Task01 security_id is not the Gold ID, but it must be stable per symbol.
    # Reject identity collisions even among rows excluded from the Gold universe.
    task_symbol_ids, task_id_symbols = {}, {}
    for row in rows:
        if not isinstance(row, dict) or _REQUIRED - row.keys():
            raise ValueError("Task01 daily row missing required fields")
        symbol = row["ts_code"]
        if not isinstance(symbol, str) or not symbol or not isinstance(row["security_id"], str) or not row["security_id"]:
            raise ValueError("invalid Task01 symbol/security_id")
        task_id = row["security_id"]
        if symbol in task_symbol_ids and task_symbol_ids[symbol] != task_id:
            raise ValueError("conflicting Task01 security_id for ts_code")
        if task_id in task_id_symbols and task_id_symbols[task_id] != symbol:
            raise ValueError("Task01 security_id reused by different symbols")
        task_symbol_ids[symbol] = task_id
        task_id_symbols[task_id] = symbol
        day = _date(row["trade_date"], "trade_date")
        if day > cutoff:
            raise ValueError("future Task01 row beyond verified pointer")
        key = (symbol, day)
        if key in seen:
            raise ValueError("duplicate Task01 daily primary key")
        seen.add(key)
        if symbol not in by_symbol:
            excluded += 1
            continue
        prices = {name: _number(row[name], name) for name in
                  ("open", "high", "low", "close", "pre_close", "change", "pct_chg")}
        if (min(prices[name] for name in ("open", "high", "low", "close")) <= 0
                or prices["high"] < max(prices[name] for name in ("open", "close", "low"))
                or prices["low"] > min(prices[name] for name in ("open", "close", "high"))
                or prices["pre_close"] <= 0):
            raise ValueError("invalid OHLC/previous_close")
        turnover, precision_loss = _shadow_turnover_cny(row["amount"])
        if precision_loss:
            turnover_rounded_rows += 1
        out.append({
            "instrument_id": by_symbol[symbol], "vendor_symbol": symbol,
            "trade_date": day.isoformat(),
            "open": float(prices["open"]), "high": float(prices["high"]),
            "low": float(prices["low"]), "close": float(prices["close"]),
            "previous_close": float(prices["pre_close"]),
            "price_change": float(prices["change"]),
            "return_pct": float(prices["pct_chg"]),
            "volume_shares": _exact_units(row["vol"], 100, "vol"),
            "turnover_cny": turnover,
            "currency": "CNY", "source_vendor": "TUSHARE",
            "is_quality_valid": False,
            "quality_flags": "SHADOW_ONLY_NOT_VALIDATED",
        })
    if not out:
        raise ValueError("no overlapping main-board daily rows")
    out.sort(key=lambda x: (x["trade_date"], x["vendor_symbol"]))
    return out, {
        "status": "SHADOW_CANDIDATE_ONLY", "source_rows": len(rows),
        "converted_rows": len(out), "excluded_outside_explicit_gold_universe": excluded,
        "turnover_cny_shadow_rounding_rule": "DECIMAL_HALF_EVEN_UNVERIFIED",
        "turnover_cny_precision_loss_rows": turnover_rounded_rows,
        "turnover_cny_rounding_contract_verified_against_gold": False,
        "point_in_time_membership_verified": False,
        "return_pct_scale_verified_against_gold": False,
        "parquet_values_compared_to_gold": False,
        "quality_validation_complete": False,
        "production_switch_allowed": False,
    }
