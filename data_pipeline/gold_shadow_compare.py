"""Read-only, deterministic OHLCV comparison of Task01 shadow rows with Gold rows.

This is an *offline diagnostic* only. It cannot verify PIT membership, corporate
actions, adjusted prices, strategy output, or approve a production switch.
Inputs must already have been independently hash-verified and normalized.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation


_FIELDS = ("open", "high", "low", "close", "volume_shares", "turnover_cny")


def _date(value):
    if isinstance(value, datetime):
        if value.time().isoformat() != "00:00:00" or value.tzinfo is not None:
            raise ValueError("trade_date must be a date without time or timezone")
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("trade_date must be ISO YYYY-MM-DD")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("invalid trade_date") from None


def _number(value, field):
    if value is None or isinstance(value, bool):
        raise ValueError(f"{field}: missing or boolean value")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f"{field}: invalid number") from None
    if not number.is_finite():
        raise ValueError(f"{field}: nonfinite number")
    return number


def _index(rows, label, cutoff):
    if not isinstance(rows, list):
        raise ValueError(f"{label}: rows must be a list")
    result = {}
    symbol_ids, id_symbols = {}, {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"{label}: invalid row")
        symbol = row.get("vendor_symbol")
        if not isinstance(symbol, str) or not symbol:
            raise ValueError(f"{label}: invalid vendor_symbol")
        instrument_id = row.get("instrument_id")
        if not isinstance(instrument_id, str) or not instrument_id:
            raise ValueError(f"{label}: missing or invalid instrument_id")
        if symbol in symbol_ids and symbol_ids[symbol] != instrument_id:
            raise ValueError(f"{label}: conflicting instrument_id for vendor_symbol")
        if instrument_id in id_symbols and id_symbols[instrument_id] != symbol:
            raise ValueError(f"{label}: instrument_id reused by different symbols")
        symbol_ids[symbol] = instrument_id
        id_symbols[instrument_id] = symbol
        day = _date(row.get("trade_date"))
        if day > cutoff:
            raise ValueError(f"{label}: future row beyond as_of")
        key = (symbol, day.isoformat())
        if key in result:
            raise ValueError(f"{label}: duplicate vendor_symbol/trade_date")
        if not all(field in row for field in _FIELDS):
            raise ValueError(f"{label}: missing OHLCV fields")
        values = {field: _number(row[field], field) for field in _FIELDS}
        if any(values[field] <= 0 for field in ("open", "high", "low", "close")):
            raise ValueError(f"{label}: nonpositive OHLC")
        if values["high"] < max(values[f] for f in ("open", "close", "low")) or values["low"] > min(values[f] for f in ("open", "close", "high")):
            raise ValueError(f"{label}: inconsistent OHLC")
        if values["volume_shares"] < 0 or values["turnover_cny"] < 0:
            raise ValueError(f"{label}: negative volume or turnover")
        if row.get("is_quality_valid") not in (True, False) or not isinstance(row.get("is_quality_valid"), bool):
            raise ValueError(f"{label}: quality flag must be boolean")
        result[key] = (values, row["is_quality_valid"], instrument_id)
    return result


def compare_shadow_daily(candidate_rows, gold_rows, *, as_of_date, sample_limit=20):
    """Return a fail-closed diagnostic report; NEVER a publish permission.

    Comparison is strict on OHLCV numeric values (no hidden tolerance).
    Even an exact match is not a five-day shadow test or production approval.
    """
    cutoff = _date(as_of_date)
    if not isinstance(sample_limit, int) or isinstance(sample_limit, bool) or not 1 <= sample_limit <= 100:
        raise ValueError("sample_limit must be 1..100")
    candidate = _index(candidate_rows, "Task01 shadow", cutoff)
    gold = _index(gold_rows, "Gold", cutoff)
    task_keys, gold_keys = set(candidate), set(gold)
    common = sorted(task_keys & gold_keys)
    missing_gold = sorted(task_keys - gold_keys)
    missing_task = sorted(gold_keys - task_keys)
    differences = []
    difference_count = 0
    instrument_id_difference_count = 0
    instrument_id_difference_examples = []
    for key in common:
        task_values, _, task_id = candidate[key]
        gold_values, _, gold_id = gold[key]
        if task_id != gold_id:
            instrument_id_difference_count += 1
            if len(instrument_id_difference_examples) < sample_limit:
                instrument_id_difference_examples.append({
                    "vendor_symbol": key[0], "trade_date": key[1],
                    "task01_instrument_id": task_id, "gold_instrument_id": gold_id,
                })
        for field in _FIELDS:
            if task_values[field] != gold_values[field]:
                difference_count += 1
                if len(differences) < sample_limit:
                    differences.append({
                        "vendor_symbol": key[0], "trade_date": key[1],
                        "field": field, "task01": str(task_values[field]),
                        "gold": str(gold_values[field]),
                    })
    gold_invalid = sum(not flag for _, flag, _ in gold.values())
    unvalidated_shadow = sum(not flag for _, flag, _ in candidate.values())
    has_differences = bool(missing_gold or missing_task or difference_count
                           or instrument_id_difference_count or gold_invalid or not common)
    # The adapter deliberately marks every shadow row as unvalidated. A price
    # match is not a quality pass: expose that state instead of a green match.
    status = ("DIFFERENCES_FOUND" if has_differences else
              "SHADOW_UNVALIDATED" if unvalidated_shadow else "MATCHED_OHLCV_ONLY")
    return {
        "schema_version": 1,
        "status": status,
        "as_of_date": cutoff.isoformat(),
        "task01_rows": len(candidate), "gold_rows": len(gold),
        "common_keys": len(common),
        "missing_in_gold": len(missing_gold),
        "missing_in_task01": len(missing_task),
        "missing_in_gold_examples": [list(k) for k in missing_gold[:sample_limit]],
        "missing_in_task01_examples": [list(k) for k in missing_task[:sample_limit]],
        "field_difference_count": difference_count,
        "field_difference_examples": differences,
        "instrument_id_difference_count": instrument_id_difference_count,
        "instrument_id_difference_examples": instrument_id_difference_examples,
        "gold_invalid_quality_rows": gold_invalid,
        "shadow_unvalidated_rows": unvalidated_shadow,
        "numeric_comparison": "EXACT_DECIMAL_NO_TOLERANCE",
        "point_in_time_membership_verified": False,
        "adjusted_prices_verified": False,
        "five_trading_days_verified": False,
        "frozen_strategy_equivalence_verified": False,
        "rollback_verified": False,
        "production_switch_allowed": False,
        "deletion_allowed": False,
    }
