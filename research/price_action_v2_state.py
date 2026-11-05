"""PA-v2 frozen three-event causal state machine (software-only).

Input rows are completed, exchange-calendar-aligned daily bars, with audited
as-of features and adjustment factors. This module does NOT calculate features,
verify production Parquet, infer execution/fills, or label future returns.
Callers must include explicit missing/suspended sessions; do not omit them.
The 2026-10-29 frozen protocol is the authority for thresholds and windows.
"""
from datetime import datetime
import math

KINDS = ("PA2_A", "PA2_B", "PA2_C")
WAIT = 5
COOLDOWN = 5
EPS = 1e-9


def _finite(value):
    if isinstance(value, bool):
        return False
    try:
        x = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(x)


def _time(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError("bar_end must be timezone-aware")
    return dt


def _bar_ok(row):
    if row.get("data_verified") is not True or row.get("session_ok") is not True:
        return False
    keys = ("open", "high", "low", "close", "factor")
    if any(not _finite(row.get(k)) or float(row[k]) <= 0 for k in keys):
        return False
    o, h, l, c = (float(row[k]) for k in ("open", "high", "low", "close"))
    return l <= min(o, c) <= max(o, c) <= h


def _features(row, keys):
    f = row.get("features")
    if row.get("features_verified") is not True or not isinstance(f, dict):
        return None
    if f.get("asof") != row["bar_end"]:
        return None
    if any(not _finite(f.get(k)) for k in keys):
        return None
    return f


def _gt(a, b):
    return a > b + EPS


def _lt(a, b):
    return a < b - EPS


def _ge(a, b):
    return a >= b - EPS


def _le(a, b):
    return a <= b + EPS


def detect(row):
    """Detect candidates at t close using ONLY caller-audited as-of features."""
    if not _bar_ok(row):
        return []
    keys = ("atr20", "h20", "l20", "h10", "sma5", "sma20",
            "sma20_prev5", "prev_close")
    f = _features(row, keys)
    if f is None or float(f["atr20"]) <= 0:
        return []
    a = float(f["atr20"])
    c = float(row["close"])
    buffer = .25 * a
    common = dict(event_time=row["bar_end"], atr20=a, buffer=buffer,
                  factor=float(row["factor"]), earliest_execution_at=None,
                  confirmed_at=None, status="active", sessions_waited=0)
    found = []
    if _gt(c, float(f["h20"]) + buffer):
        found.append(dict(common, kind="PA2_A", level=float(f["h20"])))
    if (_gt(float(f["sma5"]), float(f["sma20"]))
            and _gt(float(f["sma20"]), float(f["sma20_prev5"]))
            and _lt(c, float(f["prev_close"]))
            and _ge(float(f["h10"]) - c, a)
            and _le(float(f["h10"]) - c, 3 * a)
            and _ge(c, float(f["sma20"]) - buffer)):
        found.append(dict(common, kind="PA2_B",
                          peak=float(f["h10"]), sma20_t=float(f["sma20"]),
                          low_t=float(row["low"])))
    if _lt(c, float(f["l20"]) - buffer):
        found.append(dict(common, kind="PA2_C", level=float(f["l20"])))
    return found


def _settle(event, row, previous):
    """Return (terminal_status, reason) or (None, None); no same-bar fills."""
    if not _bar_ok(row):
        return "data_blocked", "unverified_or_missing_session"
    scale = float(row["factor"]) / event["factor"]
    c, h, l = (float(row[k]) * scale for k in ("close", "high", "low"))
    a, b = event["atr20"], event["buffer"]
    kind = event["kind"]
    if kind == "PA2_A":
        level = event["level"]
        failed = _lt(c, level - .75 * a)
        confirmed = (_le(l, level + b) and _ge(h, level - b)
                     and _ge(c, level + b))
    elif kind == "PA2_B":
        failed = (_lt(c, event["sma20_t"] - .75 * a)
                  or _lt(l, event["low_t"] - .75 * a))
        f = _features(row, ("sma5",))
        if f is None or previous is None or not _bar_ok(previous):
            return "data_blocked", "missing_asof_sma5_or_previous_bar"
        prev_high = float(previous["high"]) * float(previous["factor"]) / event["factor"]
        confirmed = (_gt(c, prev_high + b)
                     and _ge(c, float(f["sma5"]) * scale))
    elif kind == "PA2_C":
        level = event["level"]
        failed = _lt(c, level - 1.5 * a)
        confirmed = _gt(c, level + b)
    else:
        raise ValueError("unknown PA-v2 event")
    if failed and confirmed:
        return "failed", "ambiguous_same_bar"
    if failed:
        return "failed", "invalidation"
    if confirmed:
        return "confirmed", "confirmed_at_close"
    return None, None


def scan(rows):
    """Keep all eligible candidates and exclusions; no future-bar lookahead.

    Five subsequent trading sessions include day five. An incomplete tail
    remains pending. Five FULL sessions after terminal are cooldown.
    A confirmed close has no assumed fill; earliest_execution_at stays null.
    """
    events, excluded = [], []
    active = {}
    cooldown_end = {kind: -1 for kind in KINDS}
    previous_time = None
    invalid_days = 0
    for i, row in enumerate(rows):
        stamp = _time(row["bar_end"])
        if previous_time is not None and stamp <= previous_time:
            raise ValueError("daily bars must be strictly increasing")
        previous_time = stamp
        if not _bar_ok(row):
            invalid_days += 1
        for kind, event in list(active.items()):
            status, reason = _settle(event, row, rows[i - 1] if i else None)
            event["sessions_waited"] += 1
            if status is None and event["sessions_waited"] == WAIT:
                status, reason = "timeout", "five_sessions_no_confirmation"
            if status is not None:
                event.update(status=status, reason=reason, terminal_at=row["bar_end"])
                if status == "confirmed":
                    event["confirmed_at"] = row["bar_end"]
                    event["execution_policy"] = "next_legal_tradable_session_after_confirmation"
                cooldown_end[kind] = i + COOLDOWN
                del active[kind]
        for candidate in detect(row):
            kind = candidate["kind"]
            if kind in active:
                excluded.append(dict(kind=kind, event_time=row["bar_end"],
                                     reason="active_candidate"))
            elif i <= cooldown_end[kind]:
                excluded.append(dict(kind=kind, event_time=row["bar_end"],
                                     reason="cooldown"))
            else:
                events.append(candidate)
                active[kind] = candidate
    for event in active.values():
        event.update(status="pending", reason="awaiting_future_sessions")
    counts = {status: sum(e["status"] == status for e in events)
              for status in ("confirmed", "failed", "timeout", "data_blocked", "pending")}
    counts["candidates"] = len(events)
    counts["ambiguous_same_bar"] = sum(e.get("reason") == "ambiguous_same_bar"
                                       for e in events)
    counts["excluded_active"] = sum(e["reason"] == "active_candidate" for e in excluded)
    counts["excluded_cooldown"] = sum(e["reason"] == "cooldown" for e in excluded)
    return {"events": events, "excluded": excluded, "counts": counts,
            "invalid_days": invalid_days,
            "evidence_stage": "software_only_no_real_data_run"}
