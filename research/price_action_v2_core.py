"""Causal foundations for PA-v2 research; no trading signals or fill claims.

Input bar_end must be a timezone-aware ISO timestamp for a COMPLETED bar.
This module cannot verify completion against a live exchange clock.
"""
from datetime import datetime
import hashlib
import json
import math


def _time(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("bar timestamps must include a timezone")
    return result


def validate_bars(bars):
    result, previous = [], None
    for row in bars:
        stamp = _time(row["bar_end"])
        if previous is not None and stamp <= previous:
            raise ValueError("bar timestamps must be strictly increasing")
        values = {}
        for key in ("open", "high", "low", "close"):
            if isinstance(row[key], bool):
                raise ValueError("OHLC must be finite positive numbers")
            value = float(row[key])
            if not math.isfinite(value) or value <= 0:
                raise ValueError("OHLC must be finite positive numbers")
            values[key] = value
        if not (values["low"] <= min(values["open"], values["close"])
                <= max(values["open"], values["close"]) <= values["high"]):
            raise ValueError("inconsistent OHLC")
        result.append(dict(values, bar_end=row["bar_end"]))
        previous = stamp
    return result


def confirmed_swings(bars, left=2, right=2):
    """Strict extrema become observable only at right-side confirmation close.

    Tied extrema are deliberately excluded. No event is backdated as a signal.
    Earliest actual execution is UNKNOWN without calendar/tradability checks.
    Appending future bars cannot change already-confirmed events.
    """
    if (type(left) is not int or type(right) is not int
            or left < 1 or right < 1):
        raise ValueError("left/right must be positive integers")
    rows = validate_bars(bars)
    events = []
    for index in range(left, len(rows) - right):
        row = rows[index]
        neighbours = rows[index-left:index] + rows[index+1:index+right+1]
        for side, field, predicate in (
                ("high", "high", lambda a, b: a > b),
                ("low", "low", lambda a, b: a < b)):
            if all(predicate(row[field], other[field]) for other in neighbours):
                confirmed = rows[index+right]["bar_end"]
                events.append({
                    "side": side, "value": row[field],
                    "event_time": row["bar_end"], "confirmed_at": confirmed,
                    "execution_not_before": confirmed,
                    "earliest_execution_at": None,
                    "execution_policy": "next_legal_tradable_bar_after_confirmation",
                })
    return events


def candle_features(bar):
    row = validate_bars([bar])[0]
    span = row["high"] - row["low"]
    if span == 0:
        return dict(body_ratio=None, close_position=None,
                    upper_wick_ratio=None, lower_wick_ratio=None,
                    zero_range=True)
    return {
        "body_ratio": abs(row["close"]-row["open"]) / span,
        "close_position": (row["close"]-row["low"]) / span,
        "upper_wick_ratio": (row["high"]-max(row["open"], row["close"])) / span,
        "lower_wick_ratio": (min(row["open"], row["close"])-row["low"]) / span,
        "zero_range": False,
    }


def training_label_allowed(confirmed_at, label_end, test_start):
    """Reject development labels that touch or cross the test boundary."""
    confirmed, end, boundary = map(_time, (confirmed_at, label_end, test_start))
    if end < confirmed:
        raise ValueError("label end precedes confirmation")
    return confirmed < boundary and end < boundary


def rule_fingerprint(protocol):
    """Stable protocol identity, not proof that rules were frozen in advance."""
    encoded = json.dumps(protocol, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
